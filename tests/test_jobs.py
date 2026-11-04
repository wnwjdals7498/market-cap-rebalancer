import pandas as pd
import pytest

import db
import jobs
from markets import MARKETS
from sources import Quote
from brokers import SimBroker
from strategy import CASH, Config

NY = "America/New_York"
US = MARKETS["US"]


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
    src.prices = {t: 100.0 for t in US.universe} | {"IEF": 100.0}
    src.shares_out = {t: mcaps.get(t, 1.0) * 1e9 / 100 for t in US.universe}
    src.quote_time = pd.Timestamp(f"{day} 11:40", tz=NY)


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.save_positions(conn, "2026-10-26", "US", {CASH: 100_000.0}, {})
    db.save_positions(conn, "2026-10-26", "KR", {CASH: 0.0}, {})
    return conn, FakeSource(), Config(us_fee=0)


def run_day(conn, src, cfg, day, mcaps, prices=None):
    set_day(src, day, mcaps)
    src.prices |= prices or {}
    signal_now = pd.Timestamp(f"{day} 11:50", tz=NY)
    jobs.run_signal(conn, US, day, src, cfg, signal_now)
    src.quote_time = pd.Timestamp(f"{day} 12:40", tz=NY)
    jobs.run_execute(conn, US, day, src, cfg, pd.Timestamp(f"{day} 12:50", tz=NY))
    return db.latest_positions(conn, "US")[1]


def test_leader_change_moves_10pct_per_day_then_splits(env):
    conn, src, cfg = env
    a_top = {"AAPL": 5000, "NVDA": 4900}
    n_top = {"AAPL": 4900, "NVDA": 5000}
    assert run_day(conn, src, cfg, "2026-10-26", a_top) == {CASH: 0.0, "AAPL": 600, "IEF": 400}
    assert run_day(conn, src, cfg, "2026-10-27", n_top) | {CASH: 0} == {CASH: 0, "AAPL": 500, "NVDA": 100, "IEF": 400}
    assert run_day(conn, src, cfg, "2026-10-28", n_top) | {CASH: 0} == {CASH: 0, "AAPL": 400, "NVDA": 200, "IEF": 400}
    assert run_day(conn, src, cfg, "2026-10-29", n_top) | {CASH: 0} == {CASH: 0, "AAPL": 300, "NVDA": 300, "IEF": 400}
    assert db.load_state(conn, "US", "2026-10-30").mode == "SPLIT"
    # order flips inside the pair: no trades
    assert run_day(conn, src, cfg, "2026-10-30", a_top) | {CASH: 0} == {CASH: 0, "AAPL": 300, "NVDA": 300, "IEF": 400}


def test_rerun_is_noop_and_force_recomputes_from_previous_day(env):
    conn, src, cfg = env
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000})
    set_day(src, "2026-10-26", {"AAPL": 5000})
    assert "already done" in jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY))
    jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY), force=True)
    assert db.load_state(conn, "US", "2026-10-27").streak == 1


def test_stale_quotes_fail_the_run(env):
    conn, src, cfg = env
    set_day(src, "2026-10-26", {"AAPL": 5000})
    src.quote_time = pd.Timestamp("2026-10-23 15:59", tz=NY)  # last Friday's bar
    with pytest.raises(jobs.JobError, match="bond ETF"):
        jobs.run_signal(conn, US, "2026-10-26", src, cfg, pd.Timestamp("2026-10-26 11:50", tz=NY))
    assert db.run_status(conn, "signal", "US", "2026-10-26") == "FAILED"


def test_mcap_jump_holds_previous_state(env):
    conn, src, cfg = env
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000, "NVDA": 4900})
    run_day(conn, src, cfg, "2026-10-27", {"AAPL": 5000, "NVDA": 4900}, {"NVDA": 200.0})  # bad print
    assert db.run_status(conn, "signal", "US", "2026-10-27") == "SKIPPED"
    st = db.load_state(conn, "US", "2026-10-28")
    assert (st.leader, st.streak) == ("AAPL", 1)
    assert db.latest_positions(conn, "US")[1]["AAPL"] == 600


def test_share_count_jump_is_rejected(env):
    conn, src, cfg = env
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000, "NVDA": 4900})
    run_day(conn, src, cfg, "2026-10-27", {"AAPL": 5000, "NVDA": 9800})  # share count doubles
    assert db.latest_shares(conn, ["NVDA"])["NVDA"] == ("2026-10-27", 4900 * 1e9 / 100)
    assert db.load_state(conn, "US", "2026-10-28").leader == "AAPL"


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
    assert db.latest_positions(conn, "US")[1] == {CASH: 0.0, "AAPL": 300, "IEF": 200}


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
    assert [tuple(r) for r in rows] == [("AAPL", "BUY", 60, "PLANNED"), ("IEF", "BUY", 40, "PLANNED")]


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


def test_leader_change_alert(env, no_real_alerts):
    conn, src, cfg = env
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000, "NVDA": 4900})
    run_day(conn, src, cfg, "2026-10-27", {"AAPL": 4900, "NVDA": 5000})
    assert any("1위 AAPL → NVDA" in m for m in no_real_alerts)


def test_daily_report_sends_once_and_skips_quiet_days(env, no_real_alerts):
    conn, src, cfg = env
    run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000, "NVDA": 4900})
    no_real_alerts.clear()
    morning = pd.Timestamp("2026-10-27 08:05", tz="Asia/Seoul")
    out = jobs.run_report(conn, morning, fx=lambda: 1400.0)
    assert "sent" in out and len(no_real_alerts) == 1
    text = no_real_alerts[0]
    assert text.startswith("📊 일일 보고 2026-10-27 (화)")
    assert "총자산 ₩140,000,000" in text and "1위 AAPL 1일째" in text
    assert "매수 AAPL 600주 @100.00" in text and "(1·2위 차 +2.0%)" in text

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
    assert "₩140,000,000" in page and "US 2026-10-27 | SOLO" in page
    assert page.count('class="chart"') == 2  # equity history + US market caps (KR has no rankings)
    assert "<script src" not in page and "<link" not in page  # self-contained
    assert "<title>투자 리포트</title>" in webreport.write_site(conn, tmp_path / "site").read_text(encoding="utf-8")


def test_served_page_tracks_db_changes(env, tmp_path):
    import threading
    import urllib.request
    import webreport
    conn, src, cfg = env
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
    assert 'data-version' not in webreport.build_html(conn)  # written file: static, no polling
    assert "DB 없음" in webreport.render(tmp_path / "missing.db")
    server = webreport.make_server(db_path, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    get = lambda path: urllib.request.urlopen(base + path).read().decode("utf-8")
    try:
        v0 = get("/version")
        assert f'data-version="{v0}"' in get("/") and 'id="live"' in get("/")
        run_day(conn, src, cfg, "2026-10-26", {"AAPL": 5000})
        assert get("/version") != v0 and "US 2026-10-26 | SOLO" in get("/")
        with pytest.raises(urllib.error.HTTPError):
            get("/etc/passwd")
    finally:
        server.shutdown()
        server.server_close()


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
    assert "cash-limited ['AAPL']" in out and not any("PARTIAL" in m for m in no_real_alerts)
