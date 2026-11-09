import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import api
import db
import markets
import webreport

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
