import db
from strategy import LeaderState, RankState


def test_claim_run_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert db.claim_run(conn, "signal", "KR", "2026-10-27")
    assert not db.claim_run(conn, "signal", "KR", "2026-10-27")  # RUNNING
    db.finish_run(conn, "signal", "KR", "2026-10-27", "OK")
    assert not db.claim_run(conn, "signal", "KR", "2026-10-27")
    assert db.claim_run(conn, "signal", "KR", "2026-10-27", force=True)
    db.finish_run(conn, "signal", "KR", "2026-10-27", "FAILED", "boom")
    assert db.claim_run(conn, "signal", "KR", "2026-10-27")  # failed runs may retry


def test_state_roundtrip_uses_previous_session(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    st = LeaderState(mode="SPLIT", holding="AAPL", leader="NVDA", streak=3, pair=["NVDA", "AAPL"],
                     candidate="MSFT", cand_streak=1, switches=4)
    db.save_state(conn, "2026-10-26", "US", st)
    assert db.load_state(conn, "US", "2026-10-27") == st
    assert db.load_state(conn, "US", "2026-10-26") == LeaderState()  # same-day re-run starts from prior day


def test_positions_and_shares(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.save_positions(conn, "2026-10-26", "KR", {"CASH": 100.0, "005930.KS": 2}, {"005930.KS": 200_000})
    db.save_positions(conn, "2026-10-27", "KR", {"CASH": 50.0, "005930.KS": 3}, {"005930.KS": 210_000})
    assert db.latest_positions(conn, "KR") == ("2026-10-27", {"CASH": 50.0, "005930.KS": 3})

    db.save_shares(conn, "2026-10-26", {"AAPL": 1.0e9}, "test")
    db.save_shares(conn, "2026-10-27", {"AAPL": 1.1e9}, "test")
    assert db.latest_shares(conn, ["AAPL", "MSFT"], before="2026-10-26") == {"AAPL": ("2026-10-26", 1.0e9)}


def test_stale_running_run_can_be_reclaimed(tmp_path):
    from datetime import datetime, timedelta, timezone
    conn = db.connect(tmp_path / "t.db")
    t0 = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)
    assert db.claim_run(conn, "execute", "KR", "2026-11-03", now=t0)
    # process died without finish_run: blocked while it could still be alive...
    assert not db.claim_run(conn, "execute", "KR", "2026-11-03", now=t0 + timedelta(minutes=59))
    # ...then retried
    assert db.claim_run(conn, "execute", "KR", "2026-11-03", now=t0 + timedelta(minutes=61))
    db.finish_run(conn, "execute", "KR", "2026-11-03", "PARTIAL")
    assert not db.claim_run(conn, "execute", "KR", "2026-11-03", now=t0 + timedelta(hours=5))


def test_rank_state_roundtrip_uses_previous_session(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    st = RankState(confirmed=["A", "B", "C"], pending=["B", "A", "C"], streak=2)
    db.save_rank_state(conn, "2026-10-26", "US", st)
    assert db.load_rank_state(conn, "US", "2026-10-27") == st
    assert db.load_rank_state(conn, "US", "2026-10-26") == RankState()  # same-day re-run starts from prior day
    assert db.load_rank_state(conn, "KR", "2026-10-27") == RankState()


def test_last_trade_date_counts_only_ok_executes(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    for day, status in (("2026-10-26", "OK"), ("2026-10-27", "SKIPPED"), ("2026-10-28", "FAILED")):
        db.claim_run(conn, "execute", "US", day)
        db.finish_run(conn, "execute", "US", day, status)
    db.claim_run(conn, "signal", "US", "2026-10-29")
    db.finish_run(conn, "signal", "US", "2026-10-29", "OK")
    assert db.last_trade_date(conn, "US") == "2026-10-26"
    assert db.last_trade_date(conn, "US", before="2026-10-26") is None
    assert db.last_trade_date(conn, "KR") is None
