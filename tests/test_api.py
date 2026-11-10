import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import api
import db
import markets
import webreport
from strategy import RankState

TOKEN = "t" * 32
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "a.db"
    db.connect(path).close()
    return path


@pytest.fixture
def client(db_path, tmp_path):
    return TestClient(api.create_app(db_path, TOKEN, web_dir=tmp_path / "no-dist", db_timeout=0.2))


def test_api_requires_token(client):
    assert client.get("/api/v1/settings").status_code == 401
    assert client.get("/api/v1/settings", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v1/settings", headers=H).status_code == 200


@pytest.mark.parametrize("token", [None, ""])
def test_api_closed_without_configured_token(tmp_path, token):
    c = TestClient(api.create_app(tmp_path / "a.db", token=token))
    for path in ("/api/v1/status", "/api/v1/settings", "/api/v1/settings/log", "/api/v1/orders"):
        assert c.get(path, headers=H).status_code == 503
    assert c.patch("/api/v1/settings", headers=H, json={}).status_code == 503
    assert c.get("/").status_code == 200


def test_patch_settings_roundtrip(client, no_real_alerts):
    r = client.patch("/api/v1/settings", headers=H, json={"config": {"stock_pct": 70}})
    assert r.status_code == 200 and r.json()["config"]["stock_pct"] == 70
    log = client.get("/api/v1/settings/log", headers=H).json()
    assert log[0]["key"] == "config.stock_pct" and log[0]["source"] == "web"


def test_patch_rejections(client):
    r = client.patch("/api/v1/settings", headers=H, json={"trading": {"submit": True}})
    assert r.status_code == 400 and "SUBMIT" in r.json()["detail"]
    assert client.patch("/api/v1/settings", headers=H, json={"trading": {"allow_real": True}}).status_code == 400
    assert client.patch("/api/v1/settings", headers=H, json={"config": {"confirm_days": True}}).status_code == 400
    assert client.patch("/api/v1/settings", headers=H, json={"other": {}}).status_code == 400


def test_patch_when_db_locked_returns_503(client, db_path):
    lock = sqlite3.connect(db_path); lock.execute("BEGIN IMMEDIATE")
    try:
        r = client.patch("/api/v1/settings", headers=H, json={"config": {"stock_pct": 70}})
        assert r.status_code == 503 and r.json() == {"detail": "DB busy, retry"}
    finally:
        lock.rollback(); lock.close()


@pytest.mark.parametrize("path", ["/api/v1/settings/log", "/api/v1/orders"])
def test_list_limit_is_bounded(client, path):
    assert client.get(path, headers=H, params={"limit": 500}).status_code == 200
    assert client.get(path, headers=H, params={"limit": 501}).status_code == 422


def test_status_lists_markets_and_runs(client):
    s = client.get("/api/v1/status", headers=H).json()
    assert set(s["markets"]) == {"KR", "US"} and "paused" in s and isinstance(s["runs"], list)


def test_status_is_safe_under_concurrent_first_calls(tmp_path):
    markets._calendar.cache_clear()
    client = TestClient(api.create_app(tmp_path / "concurrent.db", TOKEN), raise_server_exceptions=False)
    with ThreadPoolExecutor(max_workers=32) as pool:
        statuses = list(pool.map(lambda _: client.get("/api/v1/status", headers=H).status_code, range(100)))
    assert statuses == [200] * 100


def test_openapi_and_report(client):
    assert "/api/v1/settings" in client.get("/api/openapi.json").json()["paths"]
    assert client.get("/").text.startswith("<!doctype html>")
    assert client.get("/version").status_code == 200


def test_openapi_has_typed_responses(client):
    schema = client.get("/api/openapi.json").json()
    response = schema["paths"]["/api/v1/settings"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert response["$ref"] == "#/components/schemas/Settings"
    assert schema["components"]["schemas"]["Settings"]["properties"]["trading"]["$ref"] == "#/components/schemas/Trading"


@pytest.mark.parametrize("changes", [
    {"trading": {"submit": 1}}, {"trading": {"submit": "true"}},
    {"paused": {"KR": 1}}, {"trading": {"broker": "ib"}},
    {"trading": {"unknown": True}}, {"paused": {"JP": True}},
])
def test_patch_rejects_invalid_typed_fields(client, changes):
    assert client.patch("/api/v1/settings", headers=H, json=changes).status_code == 400


def test_served_page_tracks_db_changes(client, db_path, tmp_path):
    conn = db.connect(db_path)
    assert 'data-version' not in webreport.build_html(conn)
    assert "DB 없음" in webreport.render(tmp_path / "missing.db")
    v0 = client.get("/version").text
    assert f'data-version="{v0}"' in client.get("/").text and 'id="live"' in client.get("/").text
    db.save_equity(conn, "2026-10-27", 1000, 100, 1400)
    v1 = client.get("/version").text
    assert v1 != v0 and f'data-version="{v1}"' in client.get("/").text
    assert "2026-10-27" in client.get("/").text
    assert client.get("/etc/passwd").status_code == 404
    conn.close()


def test_token_from_file_and_environment(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("MCR_API_TOKEN=file-token\n", encoding="utf-8")
    monkeypatch.delenv("MCR_API_TOKEN", raising=False)
    assert api.load_token(path) == "file-token"
    monkeypatch.setenv("MCR_API_TOKEN", "env-token")
    assert api.load_token(path) == "env-token"
    monkeypatch.setenv("MCR_API_TOKEN", "")
    assert api.load_token(path) is None


def test_settings_and_static_directory(client, tmp_path):
    assert "stock_pct" in client.get("/api/v1/settings", headers=H).json()["limits"]
    assert client.get("/api/v1/orders", headers=H).json() == []
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>설정</title>", encoding="utf-8")
    c = TestClient(api.create_app(tmp_path / "static.db", TOKEN, web_dir=dist))
    assert "<title>설정</title>" in c.get("/app/").text
    assert client.get("/app/").status_code == 404


def test_settings_ui_is_served(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>설정</title>", encoding="utf-8")
    c = TestClient(api.create_app(tmp_path / "a.db", TOKEN, web_dir=dist))
    assert "<title>설정</title>" in c.get("/app/").text
    assert TestClient(api.create_app(tmp_path / "b.db", TOKEN, web_dir=tmp_path / "missing")).get("/app/").status_code == 404


def test_settings_expose_typed_limits(client):
    body = client.get("/api/v1/settings", headers=H).json()
    assert body["limits"]["rank_weights"]["type"] == "weights"
    assert body["config"]["rank_weights"] == [50, 30, 20, 0, 0]
    assert body["limits"]["trade_every"]["options"] == ["day", "week", "month"]
    assert body["config"]["fx_enabled"] is True and body["config"]["us_universe"][0] == "AAPL"
    assert all(limit["label"] for limit in body["limits"].values())
    assert set(body["editable"]) == set(body["limits"])
    r = client.patch("/api/v1/settings", headers=H, json={"config": {"rank_weights": [40, 30, 20, 5, 5]}})
    assert r.status_code == 200 and r.json()["config"]["rank_weights"] == [40, 30, 20, 5, 5]
    assert client.patch("/api/v1/settings", headers=H, json={"config": {"rank_weights": [40, 30]}}).status_code == 400


def test_create_app_migrates_old_config_once(db_path, tmp_path):
    conn = db.connect(db_path)
    db.set_setting(conn, "config", {"pair_confirm_days": 3, "confirm_days": 3, "us_pct": 50.0})
    conn.close()
    for _ in range(2):
        c = TestClient(api.create_app(db_path, TOKEN, web_dir=tmp_path / "no-dist"))
    config = c.get("/api/v1/settings", headers=H).json()["config"]
    assert config["confirm_days"] == 0 and config["us_pct"] == 60
    assert {row["source"] for row in c.get("/api/v1/settings/log", headers=H).json()} == {"migration"}


def test_status_includes_strategy_and_fx(client, db_path):
    s = client.get("/api/v1/status", headers=H).json()
    assert s["strategy"] == {code: {"ranking": [], "confirmed": [], "targets": {}, "last_trade": None}
                             for code in ("KR", "US")}
    assert s["fx"] is None

    conn = db.connect(db_path)
    db.save_rankings(conn, "2026-11-09", "US", [("AAPL", 5e12), ("NVDA", 4e12)])
    db.save_rank_state(conn, "2026-11-09", "US", RankState(confirmed=["NVDA", "AAPL"], pending=["AAPL", "NVDA"], streak=1))
    db.save_targets(conn, "2026-11-09", "US", {"NVDA": (0.3, 100.0), "SHY": (0.4, 80.0)})
    db.claim_run(conn, "execute", "US", "2026-11-09")
    db.finish_run(conn, "execute", "US", "2026-11-09", "OK")
    db.set_setting(conn, "fx:plan", {"from": "US", "amount": 9000.0, "created": "2026-11-10", "ready": None})
    conn.close()
    s = client.get("/api/v1/status", headers=H).json()
    assert s["strategy"]["US"] == {"ranking": ["AAPL", "NVDA"], "confirmed": ["NVDA", "AAPL"],
                                   "targets": {"NVDA": 0.3, "SHY": 0.4}, "last_trade": "2026-11-09"}
    assert s["strategy"]["KR"]["ranking"] == []
    assert s["fx"] == {"from_market": "US", "amount": 9000.0, "currency": "USD", "created": "2026-11-10",
                       "ready": None, "exchange": None}
    conn = db.connect(db_path)
    db.set_setting(conn, "fx:plan", {"from": "US", "amount": 9000.0, "created": "2026-11-10",
                                     "ready": "2026-11-11", "exchange": 5200.5})
    conn.close()
    assert client.get("/api/v1/status", headers=H).json()["fx"]["exchange"] == 5200.5
