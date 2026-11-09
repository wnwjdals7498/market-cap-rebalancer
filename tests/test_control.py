import pytest

import control
import db
import jobs


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "c.db")
    yield connection
    connection.close()


def test_snapshot_defaults(conn):
    s = control.snapshot(conn)
    assert s["trading"] == {"broker": "sim", "submit": False, "allow_real": False}
    assert s["paused"] == {"KR": False, "US": False}
    assert s["config"]["stock_pct"] == 60.0 and "kr_taxable" not in s["config"]
    assert "stock_pct" in s["editable"] and "us_bond" not in s["editable"]


def test_update_logs_and_notifies_once(conn, no_real_alerts):
    control.update(conn, {"config": {"stock_pct": 70, "confirm_days": 4}, "paused": {"KR": True}}, "web")
    rows = db.setting_changes(conn)
    assert {r["key"] for r in rows} == {"config.stock_pct", "config.confirm_days", "paused.KR"}
    assert all(r["source"] == "web" for r in rows)
    assert len(no_real_alerts) == 1 and "config.stock_pct 60.0 → 70.0" in no_real_alerts[0]
    assert jobs.load_config(conn).stock_pct == 70


def test_noop_update_writes_nothing(conn, no_real_alerts):
    control.update(conn, {"config": {"stock_pct": 60.0}}, "web")
    assert db.setting_changes(conn) == [] and no_real_alerts == []


@pytest.mark.parametrize("changes", [
    {"config": {"confirm_days": True}},
    {"config": {"confirm_days": 2.5}},
    {"config": {"stock_pct": float("nan")}},
    {"config": {"stock_pct": float("inf")}},
    {"config": {"stock_pct": 101}},
    {"config": {"us_bond": "TLT"}},
    {"trading": {"broker": "ib"}},
    {"paused": {"JP": True}},
    {"other": {}},
    {"config": {"stock_pct": 70, "confirm_days": False}},
    {"trading": []},
])
def test_invalid_changes_rejected(conn, changes):
    with pytest.raises(control.ControlError):
        control.update(conn, changes, "web")
    assert db.setting_changes(conn) == []
    assert control.snapshot(conn)["config"]["stock_pct"] == 60.0


def test_submit_on_from_web_needs_confirm_and_is_atomic(conn):
    with pytest.raises(control.ControlError, match="SUBMIT"):
        control.update(conn, {"trading": {"submit": True}, "config": {"stock_pct": 70}}, "web")
    assert control.snapshot(conn)["config"]["stock_pct"] == 60.0
    control.update(conn, {"trading": {"submit": True}}, "web", confirm="SUBMIT")
    assert control.trading_settings(conn)["submit"] is True
    control.update(conn, {"trading": {"submit": False}}, "web")


def test_allow_real_is_cli_only(conn):
    with pytest.raises(control.ControlError):
        control.update(conn, {"trading": {"allow_real": True}}, "web")
    control.update(conn, {"trading": {"allow_real": True}}, "cli")
    assert control.trading_settings(conn)["allow_real"] is True
