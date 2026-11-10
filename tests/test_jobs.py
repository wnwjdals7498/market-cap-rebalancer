from dataclasses import replace

import pandas as pd
import pytest

import control
import db
import jobs
from markets import MARKETS
from sources import Quote
from brokers import SimBroker
import strategy
from strategy import CASH

NY = "America/New_York"
US = MARKETS["US"]
UNIVERSE = ("AAPL", "NVDA", "MSFT", "GOOGL", "AMZN")  # a 5-stock pool keeps the rank math readable


def Config(**changes):
    return strategy.Config(**{"us_universe": UNIVERSE} | changes)


class FakeSource:
    name = "fake"

    def __init__(self):
        self.prices: dict[str, float] = {}
        self.shares_out: dict[str, float] = {}
        self.quote_time: pd.Timestamp | None = None

    def shares(self, tickers):
        return {t: self.shares_out[t] for t in tickers if t in self.shares_out}

    def quotes(self, tickers):
        return {t: Quote(self.prices[t], self.quote_time) for t in tickers if t in self.prices}


def set_day(src, day, mcaps):
    """mcaps in billions; every stock priced at 100 so qty math stays readable."""
    src.prices = {t: 100.0 for t in UNIVERSE} | {"SHY": 100.0, "TLT": 100.0}
    src.shares_out = {t: mcaps.get(t, 1.0) * 1e9 / 100 for t in UNIVERSE}
    src.quote_time = pd.Timestamp(f"{day} 11:40", tz=NY)


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.save_positions(conn, "2026-10-26", "US", {CASH: 100_000.0}, {})
    db.save_positions(conn, "2026-10-26", "KR", {CASH: 0.0}, {})
    db.set_setting(conn, "config", {"fx_enabled": False})  # the US-only account would otherwise plan an FX move
    return conn, FakeSource(), Config(us_fee=0, trade_every="day", fx_enabled=False)


def run_day(conn, src, cfg, day, mcaps, prices=None):
    set_day(src, day, mcaps)
    src.prices |= prices or {}
    signal_now = pd.Timestamp(f"{day} 11:50", tz=NY)
    jobs.run_signal(conn, US, day, src, cfg, signal_now)
    src.quote_time = pd.Timestamp(f"{day} 12:40", tz=NY)
    jobs.run_execute(conn, US, day, src, cfg, pd.Timestamp(f"{day} 12:50", tz=NY))
    return db.latest_positions(conn, "US")[1]


TOP3 = {"AAPL": 5000, "NVDA": 4900, "MSFT": 4800}  # others tie at 1: GOOGL, AMZN keep universe order
E_TO_A = {"AMZN": 5000, "GOOGL": 4000, "MSFT": 3000, "NVDA": 2000, "AAPL": 1000}


def test_plan_orders_rounds_toward_current_holding():
    cfg = Config(min_trade_pct=0)
    assert jobs.plan_orders({"X": (.9, 1000)}, {"X": 1}, {"X": 1000}, cfg) == []
    assert jobs.plan_orders({"X": (.95, 100)}, {"X": 10}, {"X": 100}, cfg) == []
    assert jobs.plan_orders({"X": (.82, 100)}, {"X": 10}, {"X": 100}, cfg) == [("X", -1)]
    assert jobs.plan_orders({"X": (.37, 100)}, {"X": 1, CASH: 900}, {"X": 100}, cfg) == [("X", 2)]
    assert jobs.plan_orders({"X": (0, 100)}, {"X": 10}, {"X": 100}, cfg) == [("X", -10)]
    assert jobs.plan_orders({}, {"X": 10}, {"X": 100}, cfg) == [("X", -10)]
    assert jobs.plan_orders({"X": ((9 + 1e-10) / 10, 100)}, {"X": 10}, {"X": 100}, cfg) == [("X", -1)]
    assert jobs.plan_orders({"X": ((3 - 1e-10) / 10, 100)}, {"X": 1, CASH: 900}, {"X": 100}, cfg) == [("X", 2)]


def test_rank_weights_targets_on_trade_day(env):
    conn, src, cfg = env
    set_day(src, "2026-10-26", E_TO_A)
    jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY))
    targets = {t: round(w, 6) for t, (w, _) in db.load_targets(conn, "2026-10-26", "US").items()}
    assert targets == {"AMZN": 0.3, "GOOGL": 0.18, "MSFT": 0.12, "SHY": 0.4}  # first investment is uncapped
    assert db.load_rank_state(conn, "US", "2026-10-27").confirmed == ["AMZN", "GOOGL", "MSFT", "NVDA", "AAPL"]
    run_day(conn, src, cfg, "2026-10-27", E_TO_A)  # nothing changes: no further trades
    assert db.latest_positions(conn, "US")[1] == {CASH: 0.0, "AMZN": 300, "GOOGL": 180, "MSFT": 120, "SHY": 400}


def test_ranking_change_moves_at_most_step_pct_per_trade(env):
    conn, src, cfg = env
    assert run_day(conn, src, cfg, "2026-10-26", TOP3) == {CASH: 0.0, "AAPL": 300, "NVDA": 180, "MSFT": 120, "SHY": 400}
    swapped = {"AAPL": 4900, "NVDA": 5000, "MSFT": 4800}
    units = run_day(conn, src, cfg, "2026-10-27", swapped)
    # NVDA/AAPL swap weights (30% <-> 18%): sells and buys each capped at 10% of the 100k pocket
    assert units["AAPL"] == pytest.approx(200, abs=1) and units["NVDA"] == pytest.approx(280, abs=1)
    assert units["MSFT"] == 120 and units["SHY"] == 400
    units = run_day(conn, src, cfg, "2026-10-28", swapped)
    assert units["AAPL"] == pytest.approx(180, abs=1) and units["NVDA"] == pytest.approx(300, abs=1)
    assert units["SHY"] == 400


def test_rank_confirm_days_delays_the_new_ranking(env):
    conn, src, _ = env
    cfg = Config(us_fee=0, trade_every="day", confirm_days=2, step_pct=100)
    run_day(conn, src, cfg, "2026-10-26", TOP3)
    swapped = {"AAPL": 4900, "NVDA": 5000, "MSFT": 4800}
    assert run_day(conn, src, cfg, "2026-10-27", swapped)["AAPL"] == 300  # day 1 of the new order: still waiting
    state = db.load_rank_state(conn, "US", "2026-10-28")
    assert state.confirmed[:2] == ["AAPL", "NVDA"] and state.streak == 1
    assert run_day(conn, src, cfg, "2026-10-28", swapped)["NVDA"] == 300     # day 2: confirmed


def test_rerun_is_noop_and_force_recomputes_from_previous_day(env):
    conn, src, cfg = env
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000})
    set_day(src, "2026-10-26", {"AAPL": 5000})
    assert "already done" in jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY))
    jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY), force=True)
    assert db.load_rank_state(conn, "US", "2026-10-27").confirmed[0] == "AAPL"
    assert len(db.load_targets(conn, "2026-10-26", "US")) == 4  # same targets, and the executed trade is not repeated


def test_stale_quotes_fail_the_run(env):
    conn, src, cfg = env
    set_day(src, "2026-10-26", {"AAPL": 5000})
    src.quote_time = pd.Timestamp("2026-10-23 15:59", tz=NY)  # last Friday's bar
    with pytest.raises(jobs.JobError, match="bond ETF"):
        jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY))
    assert db.run_status(conn, "signal", "US", "2026-10-26") == "FAILED"


def test_mcap_jump_holds_previous_ranking(env):
    conn, src, cfg = env
    run_day(conn, src, cfg, "2026-10-26", TOP3)
    run_day(conn, src, cfg, "2026-10-27", {"AAPL": 4900, "NVDA": 5000, "MSFT": 4800}, {"GOOGL": 200.0})  # bad print
    assert db.run_status(conn, "signal", "US", "2026-10-27") == "SKIPPED"
    state = db.load_rank_state(conn, "US", "2026-10-28")
    assert state.confirmed[:3] == ["AAPL", "NVDA", "MSFT"]  # NVDA did not take 1st on the day the data was bad
    assert db.latest_positions(conn, "US")[1]["AAPL"] == 300


def test_share_count_jump_is_rejected(env):
    conn, src, cfg = env
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000, "NVDA": 4900})
    run_day(conn, src, cfg, "2026-10-27", {"AAPL": 5000, "NVDA": 9800})  # share count doubles
    assert db.latest_shares(conn, ["NVDA"])["NVDA"] == ("2026-10-27", 4900 * 1e9 / 100)
    assert db.load_rank_state(conn, "US", "2026-10-28").confirmed[0] == "AAPL"


def test_candidate_without_quote_is_ranked_out_and_weights_renormalize(env):
    conn, src, _ = env
    cfg = Config(us_fee=0, trade_every="day", rank_weights=(20, 20, 20, 20, 20))
    set_day(src, "2026-10-26", E_TO_A)
    del src.prices["AMZN"]  # e.g. no live quote yet
    jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY))
    targets = {t: round(w, 6) for t, (w, _) in db.load_targets(conn, "2026-10-26", "US").items()}
    assert targets == {"GOOGL": 0.15, "MSFT": 0.15, "NVDA": 0.15, "AAPL": 0.15, "SHY": 0.4}


def test_tick_runs_jobs_when_due(env):
    conn, src, cfg = env
    set_day(src, "2026-10-26", {"AAPL": 5000})
    assert jobs.tick(conn, src, cfg, pd.Timestamp("2026-10-26 11:00", tz=NY)) == []  # too early (KR: closed by now)
    out = jobs.tick(conn, src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY))
    assert len(out) == 1 and out[0].startswith("signal US 2026-10-26 OK")
    src.quote_time = pd.Timestamp("2026-10-26 12:40", tz=NY)
    out = jobs.tick(conn, src, cfg, pd.Timestamp("2026-10-26 12:50", tz=NY))
    assert len(out) == 1 and out[0].startswith("execute US 2026-10-26 OK")
    assert jobs.tick(conn, src, cfg, pd.Timestamp("2026-10-26 13:05", tz=NY)) == []


class AccountBroker(SimBroker):
    """SimBroker standing in for a brokerage account that also holds a stock outside the strategy."""
    name = "account"


def test_broker_holdings_drive_signal_and_unmanaged_positions_are_ignored(env):
    conn, src, cfg = env
    set_day(src, "2026-10-26", {"AAPL": 5000})
    src.prices["COIN"] = 100.0
    acct = AccountBroker({"US": {CASH: 50_000.0, "COIN": 100}}, src.prices, cfg)
    jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY), broker=acct)
    assert db.latest_positions(conn, "US") == ("2026-10-26", {CASH: 50_000.0})  # COIN not managed
    src.quote_time = pd.Timestamp("2026-10-26 12:40", tz=NY)
    jobs.run_execute(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 12:50", tz=NY), broker=acct)
    assert acct.balance("US")["COIN"] == 100  # never sold
    assert db.latest_positions(conn, "US")[1] == {CASH: 0.0, "AAPL": 150, "NVDA": 90, "MSFT": 60, "SHY": 200}


def test_dry_run_records_planned_orders_only(env):
    conn, src, cfg = env
    set_day(src, "2026-10-26", {"AAPL": 5000})
    acct = AccountBroker({"US": {CASH: 10_000.0}}, src.prices, cfg)
    jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY), broker=acct)
    src.quote_time = pd.Timestamp("2026-10-26 12:40", tz=NY)
    out = jobs.run_execute(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 12:50", tz=NY),
                           broker=acct, dry_run=True)
    assert "DRY_RUN" in out and acct.balance("US") == {CASH: 10_000.0}
    rows = conn.execute("SELECT ticker, side, qty, status FROM orders ORDER BY ticker").fetchall()
    assert [tuple(r) for r in rows] == [("AAPL", "BUY", 30, "PLANNED"), ("MSFT", "BUY", 12, "PLANNED"),
                                        ("NVDA", "BUY", 18, "PLANNED"), ("SHY", "BUY", 40, "PLANNED")]
    assert db.load_targets(conn, "2026-10-26", "US").keys() == {"AAPL", "NVDA", "MSFT", "SHY"}


def test_make_broker_defaults_and_real_money_guard(env, monkeypatch):
    conn, _, _ = env
    assert jobs.make_broker(conn) == (None, False)  # sim by default

    import kis_api
    from kis_auth import KisCredentials
    env_name = {"v": "paper"}

    class FakeClient:
        def __init__(self):
            self.creds = KisCredentials(env_name["v"], "k", "s", "50123456", "01")

    monkeypatch.setattr(kis_api, "KisClient", FakeClient)
    db.set_setting(conn, "trading", {"broker": "kis"})
    broker, dry = jobs.make_broker(conn)
    assert broker.name == "kis" and dry  # DRY_RUN unless --submit
    db.set_setting(conn, "trading", {"broker": "kis", "submit": True})
    assert jobs.make_broker(conn)[1] is False
    env_name["v"] = "real"
    with pytest.raises(jobs.JobError, match="allow-real"):
        jobs.make_broker(conn)
    db.set_setting(conn, "trading", {"broker": "kis", "submit": True, "allow_real": True})
    assert jobs.make_broker(conn)[1] is False


def test_failures_alert_once_and_trades_alert(env, no_real_alerts):
    conn, src, cfg = env
    set_day(src, "2026-10-26", {"AAPL": 5000})
    src.quote_time = pd.Timestamp("2026-10-23 15:59", tz=NY)  # stale -> fails
    for minute in ("11:50", "11:55"):
        with pytest.raises(jobs.JobError):
            jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp(f"2026-10-26 {minute}", tz=NY))
    assert len(no_real_alerts) == 1 and "FAILED" in no_real_alerts[0]
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000})
    assert any("execute US 2026-10-26 OK" in m and "BUY AAPL" in m for m in no_real_alerts)


def test_ranking_change_alert(env, no_real_alerts):
    conn, src, cfg = env
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000, "NVDA": 4900})
    assert not any("순위 변화" in m for m in no_real_alerts)  # the first ranking has nothing to change from
    run_day(conn, src, cfg, "2026-10-27", {"AAPL": 4900, "NVDA": 5000})
    assert any("순위 변화" in m and "NVDA > AAPL" in m for m in no_real_alerts)


def test_daily_report_sends_once_and_skips_quiet_days(env, no_real_alerts):
    conn, src, cfg = env
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000, "NVDA": 4900})
    no_real_alerts.clear()
    morning = pd.Timestamp("2026-10-27 08:05", tz="Asia/Seoul")
    out = jobs.run_report(conn, morning, fx=lambda: 1400.0)
    assert "sent" in out and len(no_real_alerts) == 1
    text = no_real_alerts[0]
    assert text.startswith("📊 일일 보고 2026-10-27 (화)")
    assert "총자산 ₩140,000,000" in text
    assert "순위 AAPL > NVDA > MSFT | 다음 매매 주간 | 대기 0/0일" in text
    assert "매수 AAPL 300주 @100.00" in text and "(1·2위 차 +2.0%)" in text

    assert "already done" in jobs.run_report(conn, morning, fx=lambda: 1400.0)
    next_day = pd.Timestamp("2026-10-28 08:05", tz="Asia/Seoul")
    assert "SKIPPED" in jobs.run_report(conn, next_day, fx=lambda: 1400.0)  # no jobs ran in between
    assert len(no_real_alerts) == 1

    run_day(conn, src, cfg, "2026-10-28", {"AAPL": 5000, "NVDA": 4900})
    no_real_alerts.clear()
    jobs.run_report(conn, pd.Timestamp("2026-10-29 08:05", tz="Asia/Seoul"), fx=lambda: 1414.0)
    assert "직전 +1.00%" in no_real_alerts[0] and "누적 +1.00%" in no_real_alerts[0]


def test_report_without_fx_and_tick_schedule(env, no_real_alerts):
    conn, src, cfg = env
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000})
    no_real_alerts.clear()

    def no_fx():
        raise RuntimeError("offline")

    assert jobs.tick(conn, src, cfg, pd.Timestamp("2026-10-27 07:55", tz="Asia/Seoul"), fx=no_fx) == []
    out = jobs.tick(conn, src, cfg, pd.Timestamp("2026-10-27 08:00", tz="Asia/Seoul"), fx=no_fx)
    assert any(o.startswith("report ALL 2026-10-27 OK") for o in out)
    assert "환율 조회 실패" in no_real_alerts[0]


def test_web_report_page(env, tmp_path):
    import webreport
    conn, src, cfg = env
    assert "기록 없음" in webreport.build_html(conn)  # empty DB still renders
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000, "NVDA": 4900})
    jobs.run_report(conn, pd.Timestamp("2026-10-27 08:05", tz="Asia/Seoul"), fx=lambda: 1400.0)
    run_day(conn, src, cfg, "2026-10-27", {"AAPL": 4900, "NVDA": 5000})
    jobs.run_report(conn, pd.Timestamp("2026-10-28 08:05", tz="Asia/Seoul"), fx=lambda: 1400.0)
    assert [r["date"] for r in conn.execute("SELECT date FROM equity")] == ["2026-10-27", "2026-10-28"]

    page = webreport.build_html(conn)
    assert page.startswith("<!doctype html>") and "<title>투자 리포트</title>" in page
    assert "₩140,000,000" in page and "US 2026-10-27 | 순위 NVDA &gt; AAPL" in page
    assert page.count('class="chart"') == 2  # equity history + US market caps (KR has no rankings)
    assert "<script src" not in page and "<link" not in page  # self-contained
    assert "<title>투자 리포트</title>" in webreport.write_site(conn, tmp_path / "site").read_text(encoding="utf-8")


def test_empty_account_report_is_not_a_baseline(tmp_path, no_real_alerts):
    """broker=kis starts from `init --usd 0 --krw 0`: SQLite holds CASH 0 until a submitted execute syncs the
    account, so the first reports total 0. That must not break the web page or pin the 누적 baseline at 0."""
    import webreport
    conn, src, cfg = db.connect(tmp_path / "z.db"), FakeSource(), Config(us_fee=0, fx_enabled=False)
    db.set_setting(conn, "config", {"fx_enabled": False})
    for m in ("US", "KR"):
        db.save_positions(conn, "2026-10-26", m, {CASH: 0.0}, {})
    jobs.run_report(conn, pd.Timestamp("2026-10-26 17:15", tz="Asia/Seoul"), fx=lambda: 1400.0)
    assert "누적" not in webreport.build_html(conn)  # renders (no ZeroDivisionError), no 0-based tiles

    db.save_positions(conn, "2026-10-27", "US", {CASH: 100_000.0}, {})
    run_day(conn, src, cfg, "2026-10-27", {"AAPL": 5000, "NVDA": 4900})
    jobs.run_report(conn, pd.Timestamp("2026-10-28 08:05", tz="Asia/Seoul"), fx=lambda: 1400.0)
    run_day(conn, src, cfg, "2026-10-28", {"AAPL": 5000, "NVDA": 4900})
    no_real_alerts.clear()
    jobs.run_report(conn, pd.Timestamp("2026-10-29 08:05", tz="Asia/Seoul"), fx=lambda: 1414.0)
    assert "누적 +1.00%" in no_real_alerts[0]  # first non-zero report (10-28) is the baseline
    page = webreport.build_html(conn)
    assert "2026-10-28 부터" in page and "+1.00%" in page


def test_line_chart_escapes_and_handles_gaps():
    import webreport
    svg = webreport.line_chart(["2026-01-01", "2026-01-02"], [{"name": "<b>", "slot": 0, "values": [None, 5.0]}],
                               lambda v: f"{v:.0f}")
    assert "<b>" not in svg.split("<svg")[0] and "&lt;b&gt;" in svg
    assert webreport.line_chart([], [], str).startswith('<div class="empty">')


def test_buys_capped_by_cash_are_ok_not_partial(env, no_real_alerts):
    conn, src, cfg = env
    set_day(src, "2026-10-26", {"AAPL": 5000})
    acct = AccountBroker({"US": {CASH: 10_000.0}}, src.prices, Config(us_fee=0.01))  # broker fees above the model
    jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY), broker=acct)
    src.quote_time = pd.Timestamp("2026-10-26 12:40", tz=NY)
    out = jobs.run_execute(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 12:50", tz=NY), broker=acct,
                           sleep=lambda s: None)
    assert db.run_status(conn, "execute", "US", "2026-10-26") == "OK"
    assert "cash-limited" in out and not any("PARTIAL" in m for m in no_real_alerts)


def test_paused_market_is_skipped_but_report_runs(env, no_real_alerts):
    conn, src, cfg = env
    set_day(src, "2026-10-27", {"AAPL": 5000, "NVDA": 4900})
    src.quote_time = pd.Timestamp("2026-10-27 12:45", tz=NY)       # fresh for the 12:50 tick
    at = pd.Timestamp("2026-10-27 12:50", tz=NY)                    # past US signal/execute; 01:50 KST, no report yet
    control.update(conn, {"paused": {"US": True}}, "cli")
    assert jobs.tick(conn, src, cfg, at, fx=lambda: 1400.0) == ["US paused"]
    assert db.run_status(conn, "signal", "US", "2026-10-27") is None
    control.update(conn, {"paused": {"US": False}}, "cli")
    jobs.tick(conn, src, cfg, at, fx=lambda: 1400.0)
    assert db.run_status(conn, "signal", "US", "2026-10-27") == "OK"
    control.update(conn, {"paused": {"KR": True, "US": True}}, "cli")
    out = jobs.tick(conn, src, cfg, pd.Timestamp("2026-10-28 08:05", tz="Asia/Seoul"), fx=lambda: 1400.0)
    assert "KR paused" in out                                       # KR session 10-28 not closed yet
    assert any(o.startswith("report ALL 2026-10-28") for o in out)  # pausing never stops the daily report


def test_pause_cli_and_status(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cli.db"

    def run(*args):
        monkeypatch.setattr(jobs.sys, "argv", ["jobs.py", "--db", str(path), *args])
        jobs.main()
        return capsys.readouterr().out

    run("config", "--pause", "ALL")
    assert "paused KR=True US=True" in run("status")
    run("config", "--resume", "US")
    assert "paused KR=True US=False" in run("status")
    run("config", "--resume", "KR")
    assert "paused KR=False US=False" in run("status")
    conn = db.connect(path)
    assert all(row["source"] == "cli" for row in db.setting_changes(conn))
    conn.close()


def test_web_serve_names_the_address_it_cannot_listen_on():
    # 127.0.0.1 binds (listed twice = one socket); 192.0.2.1 (TEST-NET-1) is never an address of this machine
    with pytest.raises(SystemExit, match=r"cannot listen on 192\.0\.2\.1:0 .*is this address up"):
        jobs.serve_web(object(), ["127.0.0.1", "127.0.0.1", "192.0.2.1"], 0)


def test_load_config_converts_lists_and_ignores_unknown_keys(tmp_path):
    conn = db.connect(tmp_path / "l.db")
    db.set_setting(conn, "config", {"rank_weights": [40, 30, 20, 5, 5], "kr_universe": ["000660.KS"] * 5,
                                    "kr_taxable": ["x"], "removed_key": 1})
    db.set_setting(conn, "retired", {"KR": {"207940.KS": "stock", "148070.KS": "bond"}, "US": {"IEF": "bond"}})
    cfg = control.load_config(conn)
    assert cfg.rank_weights == (40, 30, 20, 5, 5) and cfg.kr_universe == ("000660.KS",) * 5
    assert cfg.kr_taxable == {"000660.KS", "207940.KS"}


def test_non_trade_day_updates_ranking_only(env, no_real_alerts):
    conn, src, _ = env
    cfg = Config(us_fee=0)  # weekly
    run_day(conn, src, cfg, "2026-10-26", TOP3)  # Monday: the week's trade
    before = db.latest_positions(conn, "US")[1]
    no_real_alerts.clear()

    set_day(src, "2026-10-27", {"AAPL": 4900, "NVDA": 5000, "MSFT": 4800})
    out = jobs.run_signal(conn, US, "2026-10-27", src, cfg, pd.Timestamp("2026-10-27 11:50", tz=NY))
    assert "OK" in out and "no trade (week)" in out
    assert db.load_targets(conn, "2026-10-27", "US") == {}
    assert db.load_rank_state(conn, "US", "2026-10-28").confirmed[0] == "NVDA"  # ranking still tracked
    src.quote_time = pd.Timestamp("2026-10-27 12:40", tz=NY)
    out = jobs.run_execute(conn, US, "2026-10-27", src, cfg, pd.Timestamp("2026-10-27 12:50", tz=NY))
    assert "SKIPPED" in out and "매매 주기 아님" in out
    assert db.run_status(conn, "execute", "US", "2026-10-27") == "SKIPPED"
    assert db.latest_positions(conn, "US")[1] == before
    assert not any("SKIPPED" in m for m in no_real_alerts)  # a quiet day is not an alert
    assert jobs.tick(conn, src, cfg, pd.Timestamp("2026-10-27 13:05", tz=NY)) == []  # and not retried every tick

    import report
    assert "⚠️ 확인 필요" not in report.build_report(conn, "2026-10-28", 1400.0)[0]
    run_day(conn, src, cfg, "2026-11-02", TOP3)  # next week trades again
    assert db.run_status(conn, "execute", "US", "2026-11-02") == "OK"


def test_weekly_trade_retries_after_failed_first_session(env):
    conn, src, _ = env
    cfg = Config(us_fee=0)
    set_day(src, "2026-10-26", TOP3)
    src.quote_time = pd.Timestamp("2026-10-23 15:59", tz=NY)  # Monday's quotes are stale
    with pytest.raises(jobs.JobError):
        jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY))
    assert run_day(conn, src, cfg, "2026-10-27", TOP3)["AAPL"] == 300  # Tuesday trades
    assert run_day(conn, src, cfg, "2026-10-28", TOP3)["AAPL"] == 300
    assert db.run_status(conn, "execute", "US", "2026-10-28") == "SKIPPED"  # Wednesday: already traded this week


def test_trade_failure_does_not_count_as_the_weeks_trade(env):
    conn, src, _ = env
    cfg = Config(us_fee=0)
    set_day(src, "2026-10-26", TOP3)
    jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY))
    src.quote_time = pd.Timestamp("2026-10-23 15:59", tz=NY)
    with pytest.raises(jobs.JobError):
        jobs.run_execute(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 12:50", tz=NY))
    assert run_day(conn, src, cfg, "2026-10-27", TOP3)["AAPL"] == 300


def test_retired_ticker_sold_within_cap_and_dropped_at_zero(env, no_real_alerts):
    conn, src, cfg = env
    db.set_setting(conn, "retired", {"US": {"IEF": "bond"}})
    db.save_positions(conn, "2026-10-26", "US", {CASH: 57_000.0, "IEF": 430}, {"IEF": 100.0})
    set_day(src, "2026-10-26", TOP3)
    src.prices["IEF"] = 100.0
    jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY))
    src.quote_time = pd.Timestamp("2026-10-26 12:40", tz=NY)
    jobs.run_execute(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 12:50", tz=NY))
    units = db.latest_positions(conn, "US")[1]
    sold = (430 - units["IEF"]) * 100
    assert 0 < sold <= 0.10 * 100_000 + 1e-6  # within the step cap
    assert db.get_setting(conn, "retired") == {"US": {"IEF": "bond"}}

    fast = replace(cfg, step_pct=100)
    src.prices["IEF"] = 100.0
    run_day(conn, src, fast, "2026-10-27", TOP3, {"IEF": 100.0})
    assert "IEF" not in db.latest_positions(conn, "US")[1]  # last of it sold
    run_day(conn, src, fast, "2026-10-28", TOP3, {"IEF": 100.0})
    assert db.get_setting(conn, "retired")["US"] == {}
    assert any(r["key"] == "retired.US" and r["source"] == "system" for r in db.setting_changes(conn))


def test_unmanaged_position_untouched(env):
    conn, src, cfg = env
    set_day(src, "2026-10-26", TOP3)
    src.prices["COIN"] = 100.0
    acct = AccountBroker({"US": {CASH: 50_000.0, "COIN": 100}}, src.prices, cfg)
    jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY), broker=acct)
    src.quote_time = pd.Timestamp("2026-10-26 12:40", tz=NY)
    jobs.run_execute(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 12:50", tz=NY), broker=acct,
                     dry_run=True)
    assert "COIN" not in db.load_targets(conn, "2026-10-26", "US")
    assert "COIN" not in [r["ticker"] for r in conn.execute("SELECT ticker FROM orders")]
    assert "COIN" not in db.latest_positions(conn, "US")[1]


def test_kr_taxable_includes_retired_stock(tmp_path):
    conn = db.connect(tmp_path / "k.db")
    db.set_setting(conn, "retired", {"KR": {"207940.KS": "stock", "148070.KS": "bond"}})
    taxable = control.load_config(conn).kr_taxable
    assert taxable == set(Config().kr_universe) | {"207940.KS"}


# --- FX plan -----------------------------------------------------------------

FX = 1400.0


@pytest.fixture
def fx_env(env):
    """US pocket invested 100k USD (all at 100), KR pocket empty: far above the 60% US target."""
    conn, src, cfg = env
    db.set_setting(conn, "config", {"fx_enabled": True})
    cfg = replace(cfg, fx_enabled=True)
    run_day(conn, src, cfg, "2026-10-26", TOP3)
    return conn, src, cfg


def report_at(conn, day, **kw):
    return jobs.run_report(conn, pd.Timestamp(f"{day} 08:05", tz="Asia/Seoul"), fx=lambda: FX, **kw)


def fx_alerts(alerts):
    return [m for m in alerts if m.startswith("💱")]


def test_fx_plan_created_then_cash_reserved(fx_env, no_real_alerts):
    conn, src, cfg = fx_env
    report_at(conn, "2026-10-27")
    plan = db.get_setting(conn, "fx:plan")
    assert plan == {"from": "US", "amount": 10_000.0, "created": "2026-10-27", "ready": None}  # 10% of the pocket
    assert len(fx_alerts(no_real_alerts)) == 1 and "미국 포켓 $10,000.00 → 원화" in fx_alerts(no_real_alerts)[0]
    assert "💱 환전 계획" in no_real_alerts[-1]  # and the daily report carries a line

    units = run_day(conn, src, cfg, "2026-10-27", TOP3)  # next US trade day
    assert sum(w for w, _ in db.load_targets(conn, "2026-10-27", "US").values()) == pytest.approx(0.9)
    assert units[CASH] == pytest.approx(10_000)


def test_fx_plan_ready_alert_once_then_exchanged(fx_env, no_real_alerts):
    conn, src, cfg = fx_env
    report_at(conn, "2026-10-27")
    run_day(conn, src, cfg, "2026-10-27", TOP3)
    no_real_alerts.clear()

    report_at(conn, "2026-10-28", force=True)
    assert db.get_setting(conn, "fx:plan")["ready"] == "2026-10-28"
    assert db.get_setting(conn, "fx:plan")["exchange"] == 10_000.0
    notices = [m for m in no_real_alerts if m.startswith("💱 환전 안내")]
    assert len(notices) == 1 and "미국 포켓 현금 $10,000.00 를 원화로 환전하세요" in notices[0]
    no_real_alerts.clear()
    report_at(conn, "2026-10-29", force=True)  # still waiting: the daily line, no new notice
    assert fx_alerts(no_real_alerts) == [] and "💱 환전 필요" in no_real_alerts[-1]

    units = db.latest_positions(conn, "US")[1] | {CASH: 0.0}  # the user exchanged the cash
    db.save_positions(conn, "2026-10-29", "US", units, {t: 100.0 for t in units})
    no_real_alerts.clear()
    report_at(conn, "2026-10-30", force=True)
    assert db.get_setting(conn, "fx:plan") is None
    assert len(fx_alerts(no_real_alerts)) == 1 and "환전 완료" in no_real_alerts[0]


def test_fx_wait_timeout_disables_fx(fx_env, no_real_alerts):
    conn, src, cfg = fx_env
    report_at(conn, "2026-10-27")
    run_day(conn, src, cfg, "2026-10-27", TOP3)
    report_at(conn, "2026-10-28", force=True)  # ready
    report_at(conn, "2026-10-31", force=True)  # 3 days: still within fx_wait_days
    assert db.get_setting(conn, "fx:plan") is not None
    no_real_alerts.clear()
    report_at(conn, "2026-11-01", force=True)  # 4th day
    assert db.get_setting(conn, "fx:plan") is None
    assert db.get_setting(conn, "config")["fx_enabled"] is False
    assert [r["source"] for r in db.setting_changes(conn, 1)] == ["system"]
    assert any("환전 대기 4일 초과" in m for m in no_real_alerts)
    report_at(conn, "2026-11-02", force=True)  # switched off: no new plan
    assert db.get_setting(conn, "fx:plan") is None

    units = run_day(conn, src, cfg, "2026-11-02", TOP3)  # next trade day reinvests the cash
    assert units[CASH] == pytest.approx(0, abs=1)


def test_fx_plan_cleared_when_ratio_recovers(fx_env, no_real_alerts):
    conn, src, cfg = fx_env
    report_at(conn, "2026-10-27")
    db.save_positions(conn, "2026-10-27", "KR", {CASH: 93_000_000.0}, {})  # KR funded: US 60.1%
    no_real_alerts.clear()
    report_at(conn, "2026-10-28", force=True)
    assert db.get_setting(conn, "fx:plan") is None
    assert len(fx_alerts(no_real_alerts)) == 1 and "비율 회복" in no_real_alerts[0]


def test_fx_reserve_only_for_the_source_market(fx_env):
    conn, src, cfg = fx_env
    db.set_setting(conn, "fx:plan", {"from": "KR", "amount": 5_000_000.0, "created": "2026-10-27", "ready": None})
    run_day(conn, src, cfg, "2026-10-27", TOP3)
    assert sum(w for w, _ in db.load_targets(conn, "2026-10-27", "US").values()) == pytest.approx(1.0)
    db.set_setting(conn, "fx:plan", {"from": "US", "amount": 10_000.0, "created": "2026-10-27", "ready": "2026-10-28"})
    run_day(conn, src, replace(cfg, fx_enabled=False), "2026-10-28", TOP3)  # FX switched off: nothing reserved
    assert sum(w for w, _ in db.load_targets(conn, "2026-10-28", "US").values()) == pytest.approx(1.0)


def test_ready_plan_keeps_cash_reserved_on_daily_trade_days(fx_env, no_real_alerts):
    conn, src, cfg = fx_env  # trade_every="day"
    report_at(conn, "2026-10-27")
    run_day(conn, src, cfg, "2026-10-27", TOP3)
    report_at(conn, "2026-10-28", force=True)
    assert db.get_setting(conn, "fx:plan")["ready"] == "2026-10-28"

    units = run_day(conn, src, cfg, "2026-10-28", TOP3)  # the next daily trade day: the cash stays put
    assert units[CASH] == pytest.approx(10_000)
    assert sum(w for w, _ in db.load_targets(conn, "2026-10-28", "US").values()) == pytest.approx(0.9)
    no_real_alerts.clear()
    report_at(conn, "2026-10-29", force=True)
    plan = db.get_setting(conn, "fx:plan")  # still waiting, not mistaken for an exchange
    assert plan and plan["ready"] == "2026-10-28" and fx_alerts(no_real_alerts) == []
    assert "💱 환전 필요" in no_real_alerts[-1]


def test_report_print_does_not_touch_the_plan(fx_env, no_real_alerts):
    conn, src, cfg = fx_env
    no_real_alerts.clear()
    jobs.run_report(conn, pd.Timestamp("2026-10-27 08:05", tz="Asia/Seoul"), fx=lambda: FX, send=False)
    assert db.get_setting(conn, "fx:plan") is None and no_real_alerts == []


def test_no_fx_plan_when_total_zero(env, no_real_alerts):
    conn, src, cfg = env
    db.set_setting(conn, "config", {"fx_enabled": True})
    db.save_positions(conn, "2026-10-26", "US", {CASH: 0.0}, {})
    report_at(conn, "2026-10-27", force=True)
    assert db.get_setting(conn, "fx:plan") is None and fx_alerts(no_real_alerts) == []


@pytest.mark.parametrize("enabled, us, kr", [(False, 100_000.0, 0.0),            # switched off
                                              (True, 60_000.0, 56_000_000.0),      # exactly 60/40
                                              (True, 64_000.0, 50_400_000.0)])     # 64.0% US: inside the 5pp band
def test_no_fx_plan_when_disabled_or_in_band(env, no_real_alerts, enabled, us, kr):
    conn, src, cfg = env
    db.set_setting(conn, "config", {"fx_enabled": enabled})
    db.save_positions(conn, "2026-10-26", "US", {CASH: us}, {})
    db.save_positions(conn, "2026-10-26", "KR", {CASH: kr}, {})
    report_at(conn, "2026-10-27", force=True)
    assert db.get_setting(conn, "fx:plan") is None and fx_alerts(no_real_alerts) == []


def test_init_writes_the_new_config_and_needs_no_migration(tmp_path, monkeypatch, capsys):
    path = tmp_path / "init.db"
    for args in (("init", "--usd", "1000", "--krw", "0", "--confirm-days", "2"), ("status",)):
        monkeypatch.setattr(jobs.sys, "argv", ["jobs.py", "--db", str(path), *args])
        jobs.main()
        capsys.readouterr()
    conn = db.connect(path)
    saved = db.get_setting(conn, "config")
    assert saved["confirm_days"] == 2 and saved["rank_weights"] == [50, 30, 20, 0, 0]
    assert not set(control.OLD_KEYS) & set(saved)
    assert db.get_setting(conn, "config:version") == control.CONFIG_VERSION
    assert db.setting_changes(conn) == [] and db.get_setting(conn, "retired") is None
    conn.close()


def test_old_strategy_execute_does_not_count_as_this_weeks_trade(env):
    conn, src, _ = env
    cfg = Config(us_fee=0, fx_enabled=False)  # weekly
    db.set_setting(conn, "config", {"pair_confirm_days": 3, "confirm_days": 3, "us_pct": 50.0, "fx_enabled": False})
    db.claim_run(conn, "execute", "US", "2026-11-09")  # Monday: the old strategy's last trade
    db.finish_run(conn, "execute", "US", "2026-11-09", "OK")
    assert control.migrate(conn, today="2026-11-10") is True
    assert db.last_trade_date(conn, "US") is None
    set_day(src, "2026-11-10", TOP3)
    jobs.run_signal(conn, US, "2026-11-10", src, cfg, pd.Timestamp("2026-11-10 11:50", tz=NY))  # Tuesday, same ISO week
    assert db.load_targets(conn, "2026-11-10", "US")
    db.claim_run(conn, "execute", "US", "2026-11-10")
    db.finish_run(conn, "execute", "US", "2026-11-10", "OK")
    assert db.last_trade_date(conn, "US") == "2026-11-10"


def test_fx_ready_at_half_the_amount_and_exchange_is_the_cash_on_hand(fx_env, no_real_alerts):
    conn, src, cfg = fx_env
    report_at(conn, "2026-10-27")  # amount $10,000
    units = db.latest_positions(conn, "US")[1]
    px = {t: 100.0 for t in units if t != CASH}
    db.save_positions(conn, "2026-10-27", "US", units | {CASH: 4_000.0}, px)
    report_at(conn, "2026-10-28", force=True)  # 40% of the amount: not ready
    assert db.get_setting(conn, "fx:plan")["ready"] is None
    db.save_positions(conn, "2026-10-28", "US", units | {CASH: 6_000.0}, px)
    no_real_alerts.clear()
    report_at(conn, "2026-10-29", force=True)  # 60%: ready, and the user is told to exchange what is there
    plan = db.get_setting(conn, "fx:plan")
    assert plan["ready"] == "2026-10-29" and plan["exchange"] == 6_000.0 and plan["amount"] == 10_000.0
    assert any("현금 $6,000.00 를 원화로 환전하세요" in m for m in no_real_alerts)
    assert "$6,000.00 를 원화로 환전하세요" in no_real_alerts[-1]
    db.save_positions(conn, "2026-10-29", "US", units | {CASH: 2_900.0}, px)
    report_at(conn, "2026-10-30", force=True)  # below half of what was set aside: exchanged
    assert db.get_setting(conn, "fx:plan") is None


def test_exchanged_ready_plan_is_not_rebuilt_by_the_next_daily_signal(fx_env, no_real_alerts):
    conn, src, cfg = fx_env  # trade_every="day"
    report_at(conn, "2026-10-27")
    run_day(conn, src, cfg, "2026-10-27", TOP3)
    report_at(conn, "2026-10-28", force=True)
    assert db.get_setting(conn, "fx:plan")["ready"] == "2026-10-28"

    units = db.latest_positions(conn, "US")[1] | {CASH: 0.0}  # the user exchanged the cash
    db.save_positions(conn, "2026-10-28", "US", units, {t: 100.0 for t in units})
    after = run_day(conn, src, cfg, "2026-10-29", TOP3)
    assert sum(w for w, _ in db.load_targets(conn, "2026-10-29", "US").values()) == pytest.approx(1.0)
    assert {t: q for t, q in after.items() if t != CASH} == {t: q for t, q in units.items() if t != CASH}  # nothing sold
    report_at(conn, "2026-10-30", force=True)
    assert db.get_setting(conn, "fx:plan") is None
    assert db.get_setting(conn, "config")["fx_enabled"] is True
    assert any("환전 완료" in m for m in no_real_alerts)


def test_execute_drops_targets_the_strategy_no_longer_manages(env):
    conn, src, cfg = env
    set_day(src, "2026-10-26", TOP3)
    src.prices["META"] = 100.0
    jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY))
    targets = db.load_targets(conn, "2026-10-26", "US")
    db.save_targets(conn, "2026-10-26", "US", targets | {"META": (0.2, 100.0)})  # candidates changed since the signal
    src.quote_time = pd.Timestamp("2026-10-26 12:40", tz=NY)
    acct = AccountBroker({"US": {CASH: 100_000.0}}, src.prices, cfg)
    jobs.run_execute(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 12:50", tz=NY), broker=acct,
                     dry_run=True)
    tickers = {r["ticker"] for r in conn.execute("SELECT ticker FROM orders")}
    assert tickers == {"AAPL", "NVDA", "MSFT", "SHY"}

    db.save_targets(conn, "2026-10-27", "US", {"META": (1.0, 100.0)})
    db.claim_run(conn, "signal", "US", "2026-10-27")
    db.finish_run(conn, "signal", "US", "2026-10-27", "OK")
    set_day(src, "2026-10-27", TOP3)
    with pytest.raises(jobs.JobError, match="no managed ticker"):  # never sell everything for lack of targets
        jobs.run_execute(conn, US, "2026-10-27", src, cfg, pd.Timestamp("2026-10-27 12:50", tz=NY), broker=acct,
                         dry_run=True)
