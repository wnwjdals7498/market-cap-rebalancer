import pytest

import control
import db
import jobs
from strategy import Config


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
    assert "rank_weights" in s["editable"] and "pair_confirm_days" not in s["editable"]


def test_update_logs_and_notifies_once(conn, no_real_alerts):
    control.update(conn, {"config": {"stock_pct": 70, "confirm_days": 4}, "paused": {"KR": True}}, "web")
    rows = db.setting_changes(conn)
    assert {r["key"] for r in rows} == {"config.stock_pct", "config.confirm_days", "paused.KR"}
    assert all(r["source"] == "web" for r in rows)
    assert len(no_real_alerts) == 1 and "config.stock_pct 60.0 → 70.0" in no_real_alerts[0]
    assert control.load_config(conn).stock_pct == 70


def test_noop_update_writes_nothing(conn, no_real_alerts):
    control.update(conn, {"config": {"stock_pct": 60.0}}, "web")
    assert db.setting_changes(conn) == [] and no_real_alerts == []


@pytest.mark.parametrize("changes", [
    {"config": {"confirm_days": True}},
    {"config": {"confirm_days": 2.5}},
    {"config": {"stock_pct": float("nan")}},
    {"config": {"stock_pct": float("inf")}},
    {"config": {"stock_pct": 101}},
    {"config": {"pair_confirm_days": 3}},
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


@pytest.mark.parametrize("source", ["web", "cli"])
def test_web_cannot_start_sending_orders_without_confirm(conn, source):
    control.update(conn, {"trading": {"submit": True}}, source, confirm="SUBMIT")
    before = control.snapshot(conn)
    log = db.setting_changes(conn)
    with pytest.raises(control.ControlError, match="SUBMIT"):
        control.update(conn, {"trading": {"broker": "kis"}, "config": {"stock_pct": 70}}, "web")
    assert control.snapshot(conn) == before
    assert db.setting_changes(conn) == log
    control.update(conn, {"trading": {"broker": "kis"}}, "web", confirm="SUBMIT")
    assert control.trading_settings(conn)["broker"] == "kis"
    control.update(conn, {"trading": {"broker": "sim"}}, "web")
    control.update(conn, {"trading": {"broker": "kis"}}, "web", confirm="SUBMIT")
    control.update(conn, {"trading": {"submit": False}}, "web")
    assert control.trading_settings(conn) == {"broker": "kis", "submit": False, "allow_real": False}


def test_typed_settings_validation(conn):
    def bad(changes):
        with pytest.raises(control.ControlError):
            control.update(conn, {"config": changes}, "web")

    bad({"rank_weights": [50, 30, 20, 0, 1]})            # sums to 101
    bad({"rank_weights": [49, 30, 20, 0, 0]})            # sums to 99
    bad({"rank_weights": [50, 30, 10, 5, 3, 2]})         # 6 slots
    bad({"rank_weights": [110, -10, 0, 0, 0]})           # out of range
    bad({"trade_every": "year"})
    bad({"fx_enabled": 1})
    bad({"fx_wait_days": 0})
    bad({"kr_universe": ["5930.KS", "000660.KS", "402340.KS", "373220.KS", "005380.KS"]})
    bad({"kr_universe": ["005930.KS", "005930.KS", "402340.KS", "373220.KS", "005380.KS"]})
    bad({"us_universe": ["AAPL", "NVDA", "MSFT", "GOOGL"]})
    bad({"us_short_bond": "AAPL"})                        # overlaps the stock candidates
    bad({"us_short_bond": "TLT"})                         # same as the long bond
    bad({"us_short_bond": "shy"})
    assert db.setting_changes(conn) == [] and db.get_setting(conn, "retired") is None

    control.update(conn, {"config": {"rank_weights": [50, 30, 20, 0, 0], "trade_every": "month"}}, "web")
    assert db.get_setting(conn, "config")["trade_every"] == "month"
    assert control.update(conn, {"config": {"rank_weights": [40, 30, 20, 5, 5]}}, "web")["config"][
        "rank_weights"] == [40, 30, 20, 5, 5]
    assert control.load_config(conn).rank_weights == (40, 30, 20, 5, 5)


def test_system_and_migration_sources_are_allowed(conn):
    control.update(conn, {"config": {"fx_enabled": False}}, "system")
    assert db.setting_changes(conn)[0]["source"] == "system"
    with pytest.raises(control.ControlError):
        control.update(conn, {"config": {"fx_enabled": True}}, "other")


def test_universe_change_retires_removed_tickers(conn):
    us = list(Config().us_universe)
    control.update(conn, {"config": {"us_universe": [t if t != "AMZN" else "META" for t in us]}}, "web")
    assert db.get_setting(conn, "retired")["US"] == {"AMZN": "stock"}
    control.update(conn, {"config": {"us_short_bond": "IEF"}}, "web")
    assert db.get_setting(conn, "retired")["US"] == {"AMZN": "stock", "SHY": "bond"}
    control.update(conn, {"config": {"us_universe": us}}, "web")  # AMZN is back, META drops out
    assert db.get_setting(conn, "retired")["US"] == {"SHY": "bond", "META": "stock"}


OLD_CONFIG = {"stock_pct": 60.0, "us_pct": 50.0, "fx_mode": "separate", "band_pp": 0.0, "step_pct": 10.0,
              "confirm_days": 3, "pair_confirm_days": 3, "solo_after_days": 0, "us_bond": "IEF",
              "kr_bond": "148070.KS"}


def test_migrate_fresh_db_only_sets_version(conn, no_real_alerts):
    assert control.migrate(conn) is False
    assert db.get_setting(conn, "config:version") == 2
    assert db.get_setting(conn, "retired") is None and db.setting_changes(conn) == []
    assert db.get_setting(conn, "rank:since") is None  # a new DB has no old-strategy trades to ignore
    new_style = {k: v for k, v in OLD_CONFIG.items() if k not in control.OLD_KEYS}
    db.set_setting(conn, "config:version", 1)
    db.set_setting(conn, "config", new_style)  # as `init` writes once the old fields are gone
    assert control.migrate(conn) is False
    assert db.get_setting(conn, "config") == new_style
    assert db.setting_changes(conn) == [] and no_real_alerts == []


def test_migrate_old_config_once(conn, no_real_alerts):
    db.set_setting(conn, "config", OLD_CONFIG)
    assert control.migrate(conn, today="2026-11-10") is True
    assert db.get_setting(conn, "rank:since") == "2026-11-10"
    cfg = control.load_config(conn)
    assert cfg.confirm_days == 0 and cfg.us_pct == 60
    assert not set(control.OLD_KEYS) & set(db.get_setting(conn, "config"))
    retired = db.get_setting(conn, "retired")
    assert retired["US"] == {"IEF": "bond", "META": "stock", "AVGO": "stock", "TSLA": "stock"}
    assert retired["KR"] == {"207940.KS": "stock", "148070.KS": "bond"}
    assert cfg.kr_taxable == set(cfg.kr_universe) | {"207940.KS"}
    log = db.setting_changes(conn)
    assert log and {r["source"] for r in log} == {"migration"}
    assert db.get_setting(conn, "config:version") == 2 and len(no_real_alerts) == 1

    assert control.migrate(conn, today="2026-11-20") is False
    assert db.get_setting(conn, "rank:since") == "2026-11-10"  # set once
    assert db.setting_changes(conn) == log and len(no_real_alerts) == 1
    assert db.get_setting(conn, "retired") == retired
