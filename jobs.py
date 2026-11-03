"""Daily routine CLI (phase 1: yfinance data, paper account in SQLite).

    python jobs.py init --usd 35000 --krw 50000000 [--stock-pct 60]
    python jobs.py signal --market KR [--date 2026-10-27] [--force]   # 시총 조회 -> 순위 -> 목표 비중
    python jobs.py execute --market KR [--date 2026-10-27] [--force]  # 목표 비중대로 모의 체결
    python jobs.py tick                                               # 지금 할 일 실행 (스케줄러용)
    python jobs.py status
"""
from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import sys
from dataclasses import asdict, fields
from datetime import timedelta
from pathlib import Path

import pandas as pd

import data
import db
from markets import MARKETS, Market, current_session, session_times
from sources import FallbackSource, Quote, Source, YFinanceSource
from strategy import CASH, Config, LeaderState, plan_targets, pocket_value, trade_cost, update_state

log = logging.getLogger("mcr")

MAX_QUOTE_AGE = timedelta(minutes=40)  # yfinance KRX quotes lag ~20 min
SHARES_JUMP = 0.20                     # reject a new share count that moves more than this vs the last one
MCAP_JUMP = 0.40                       # day-over-day market-cap move treated as bad data
PRICE_BAND = 0.05                      # limit price = market price +/- 5%


class JobError(RuntimeError):
    pass


def load_config(conn: sqlite3.Connection) -> Config:
    known = {f.name for f in fields(Config)}
    saved = {k: v for k, v in db.get_setting(conn, "config", {}).items() if k in known}
    return Config(**saved, kr_taxable=frozenset(data.KR_UNIVERSE))


def bond_of(mkt: Market, cfg: Config) -> str:
    return cfg.us_bond if mkt.code == "US" else cfg.kr_bond


def fresh_quotes(src: Source, tickers: list[str], session: str, tz: str, now: pd.Timestamp) -> dict[str, Quote]:
    """Quotes from today's session that are recent enough; others are dropped."""
    out = {}
    for t, q in src.quotes(tickers).items():
        local = q.time.tz_convert(tz)
        if local.strftime("%Y-%m-%d") == session and now - q.time <= MAX_QUOTE_AGE:
            out[t] = q
        else:
            log.warning("stale quote %s @ %s", t, local)
    return out


def current_weights(state: LeaderState) -> dict[str, float]:
    if state.mode == "SPLIT" and state.pair:
        return {t: 0.5 for t in state.pair}
    return {state.leader or state.holding: 1.0}


def refresh_shares(conn: sqlite3.Connection, mkt: Market, session: str, src: Source) -> dict[str, float]:
    universe = list(mkt.universe)
    known = db.latest_shares(conn, universe, before=session, source=src.name)
    if all(d == session for d, _ in known.values()) and len(known) == len(universe):
        return {t: s for t, (_, s) in known.items()}
    fetched = src.shares(universe)
    accepted = {}
    for t in universe:
        new, prev = fetched.get(t), known.get(t, (None, None))[1]
        if new and prev and abs(new / prev - 1) > SHARES_JUMP:
            log.warning("shares jump %s %.0f -> %.0f, keeping previous", t, prev, new)
            new = prev
        if new or prev:
            accepted[t] = new or prev
    db.save_shares(conn, session, accepted, src.name)
    return accepted


def _guarded(job: str, conn: sqlite3.Connection, mkt: Market, session: str, force: bool, fn) -> str:
    if not db.claim_run(conn, job, mkt.code, session, force):
        return f"{job} {mkt.code} {session}: already done"
    try:
        status, detail = fn()
    except Exception as e:
        db.finish_run(conn, job, mkt.code, session, "FAILED", repr(e))
        if isinstance(e, JobError):
            log.error("%s %s %s failed: %s", job, mkt.code, session, e)
        else:
            log.exception("%s %s %s failed", job, mkt.code, session)
        raise
    db.finish_run(conn, job, mkt.code, session, status, detail)
    log.info("%s %s %s %s: %s", job, mkt.code, session, status, detail)
    return f"{job} {mkt.code} {session} {status}: {detail}"


def run_signal(conn: sqlite3.Connection, mkt: Market, session: str, src: Source, cfg: Config,
               now: pd.Timestamp, force: bool = False) -> str:
    def work():
        tz = session_times(mkt, session).open.tz
        bond = bond_of(mkt, cfg)
        _, units = db.latest_positions(conn, mkt.code)
        if not units:
            raise JobError("no positions; run `jobs.py init` first")
        held = [t for t in units if t != CASH and t not in mkt.universe and t != bond]
        shares = refresh_shares(conn, mkt, session, src)
        quotes = fresh_quotes(src, list(mkt.universe) + [bond] + held, session, str(tz), now)
        if bond not in quotes:
            raise JobError(f"no fresh quote for bond ETF {bond}")

        prev = db.previous_mcaps(conn, mkt.code, session)
        rows, valid, suspect = [], {}, []
        for t in mkt.universe:
            q = quotes.get(t)
            if q is None or t not in shares:
                continue
            mcap = q.price * shares[t]
            flag = None
            if t in prev and abs(mcap / prev[t] - 1) > MCAP_JUMP:
                flag = f"mcap jump {prev[t]:.3g} -> {mcap:.3g}"
                suspect.append(t)
            else:
                valid[t] = mcap
            rows.append({"ticker": t, "price": q.price, "quote_time": q.time.isoformat(), "shares": shares[t],
                         "mcap": mcap, "source": q.source or src.name, "flag": flag})
        rows.append({"ticker": bond, "price": quotes[bond].price, "quote_time": quotes[bond].time.isoformat(),
                     "source": quotes[bond].source or src.name})
        db.save_snapshot(conn, session, mkt.code, rows)

        ranked = sorted(valid.items(), key=lambda kv: kv[1], reverse=True)
        db.save_rankings(conn, session, mkt.code, ranked)
        state = db.load_state(conn, mkt.code, session)
        if suspect or len(ranked) < 2:
            # bad or missing data: keep yesterday's ranking memory, only re-apply its weights
            if state.leader is None:
                raise JobError(f"not enough clean market caps on first run (suspect={suspect})")
            weights, note = current_weights(state), f"HOLD (suspect={suspect}, ranked={len(ranked)})"
        else:
            weights, note = update_state(state, [t for t, _ in ranked], cfg), "OK"
        db.save_state(conn, session, mkt.code, state)

        prices = {t: q.price for t, q in quotes.items()}
        missing = [t for t in units if t != CASH and t not in prices]
        if missing:
            raise JobError(f"no fresh quote for held {missing}")
        value = pocket_value(units, prices)
        target = plan_targets(units, prices, weights, bond, cfg, value)
        db.save_targets(conn, session, mkt.code, {t: (v / value, prices[t]) for t, v in target.items()})

        top = " > ".join(t for t, _ in ranked[:3])
        tw = ", ".join(f"{t} {v / value:.1%}" for t, v in sorted(target.items(), key=lambda kv: -kv[1]))
        return ("OK" if note == "OK" else "SKIPPED"), f"{note}; rank {top}; mode {state.mode}; target {tw}"

    return _guarded("signal", conn, mkt, session, force, work)


def run_execute(conn: sqlite3.Connection, mkt: Market, session: str, src: Source, cfg: Config,
                now: pd.Timestamp, force: bool = False) -> str:
    """Paper execution: integer shares at the current quote, sells before buys."""
    def work():
        if db.run_status(conn, "signal", mkt.code, session) not in ("OK", "SKIPPED"):
            raise JobError("signal has not completed for this session")
        targets = db.load_targets(conn, session, mkt.code)
        _, units = db.latest_positions(conn, mkt.code)
        tickers = sorted((set(targets) | set(units)) - {CASH})
        tz = str(session_times(mkt, session).open.tz)
        quotes = fresh_quotes(src, tickers, session, tz, now)
        missing = [t for t in tickers if t not in quotes]
        if missing:
            raise JobError(f"no fresh quote for {missing}")
        prices = {t: q.price for t, q in quotes.items()}
        value = pocket_value(units, prices)
        min_trade = cfg.min_trade_pct / 100 * value

        orders = []
        for t in tickers:
            weight = targets.get(t, (0.0, 0.0))[0]
            want = math.floor(weight * value / prices[t])
            delta = want - int(units.get(t, 0))
            if delta and (want == 0 or abs(delta) * prices[t] >= min_trade):
                orders.append((t, delta))

        cash = units.get(CASH, 0.0)
        new_units = dict(units)
        fills = []
        for t, delta in sorted(orders, key=lambda o: o[1]):  # sells (negative) first
            price = prices[t]
            if delta > 0:
                affordable = math.floor(cash / (price + trade_cost(cfg, mkt.code, t, price)))
                delta = min(delta, affordable)
                if delta <= 0:
                    continue
            notional = delta * price
            cost = trade_cost(cfg, mkt.code, t, notional)
            cash -= notional + cost
            new_units[t] = new_units.get(t, 0) + delta
            side = "BUY" if delta > 0 else "SELL"
            limit = price * (1 + PRICE_BAND) if delta > 0 else price * (1 - PRICE_BAND)
            db.save_order(conn, {"session_date": session, "market": mkt.code, "ticker": t, "side": side,
                                 "qty": abs(delta), "ref_price": price, "limit_price": limit,
                                 "fill_price": price, "cost": cost, "status": "FILLED"})
            fills.append(f"{side} {t} {abs(delta)}@{price:,.2f}")
        new_units[CASH] = cash
        db.save_positions(conn, session, mkt.code, new_units, prices)
        return "OK", ("; ".join(fills) or "no orders") + f"; value {pocket_value(new_units, prices):,.0f}"

    return _guarded("execute", conn, mkt, session, force, work)


def tick(conn: sqlite3.Connection, src: Source, cfg: Config, now: pd.Timestamp) -> list[str]:
    """Run whatever is due now. Safe to call every few minutes; runs table prevents repeats."""
    out = []
    for mkt in MARKETS.values():
        s = current_session(mkt, now)
        if s is None or now >= s.close:
            continue
        try:
            if now >= s.signal and db.run_status(conn, "signal", mkt.code, s.session) not in ("OK", "SKIPPED"):
                out.append(run_signal(conn, mkt, s.session, src, cfg, now))
            if now >= s.execute and now < s.close - timedelta(minutes=15) \
                    and db.run_status(conn, "execute", mkt.code, s.session) != "OK":
                out.append(run_execute(conn, mkt, s.session, src, cfg, now))
        except Exception as e:  # keep the other market going
            out.append(f"{mkt.code} {s.session} FAILED: {e!r}")
    return out


def make_source(name: str) -> Source:
    """kis: KIS quotes (+ KR shares), yfinance for US shares and for anything KIS fails on.
    auto: kis when .env credentials load, else yfinance."""
    yf_src = YFinanceSource()
    if name == "yfinance":
        return yf_src
    from kis_api import KisClient, KisSource
    from kis_auth import KisAuthError
    try:
        client = KisClient()
    except KisAuthError as e:
        if name == "kis":
            raise
        log.info("KIS credentials unavailable (%s); using yfinance", e)
        return yf_src
    return FallbackSource(KisSource(client, us_shares=yf_src), yf_src)


def status(conn: sqlite3.Connection) -> str:
    lines = []
    for code in MARKETS:
        lines.append(f"[{code}]")
        for r in conn.execute("SELECT * FROM runs WHERE market = ? ORDER BY session_date DESC, job DESC LIMIT 4",
                              (code,)):
            lines.append(f"  run {r['session_date']} {r['job']:7s} {r['status']:7s} {r['detail'] or ''}")
        date, units = db.latest_positions(conn, code)
        lines.append(f"  positions@{date}: " + ", ".join(f"{t} {q:,.2f}" for t, q in units.items()))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(db.DEFAULT_DB))
    ap.add_argument("--source", choices=["auto", "kis", "yfinance"], default="auto",
                    help="quote source (auto: KIS if .env credentials exist)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="create paper account / save config")
    p.add_argument("--usd", type=float, required=True)
    p.add_argument("--krw", type=float, required=True)
    for name in ("stock_pct", "step_pct", "min_trade_pct"):
        p.add_argument("--" + name.replace("_", "-"), type=float)
    for name in ("confirm_days", "pair_confirm_days", "solo_after_days"):
        p.add_argument("--" + name.replace("_", "-"), type=int)
    for job in ("signal", "execute"):
        p = sub.add_parser(job)
        p.add_argument("--market", choices=list(MARKETS), required=True)
        p.add_argument("--date", help="session date (exchange local), default today")
        p.add_argument("--force", action="store_true")
    sub.add_parser("tick")
    sub.add_parser("status")
    a = ap.parse_args()

    logs = Path(__file__).with_name("logs")
    logs.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(logs / "jobs.log", encoding="utf-8")]
    if sys.stderr is not None:  # None under pythonw (scheduled task)
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    conn = db.connect(a.db)
    now = pd.Timestamp.now(tz="UTC")

    if a.cmd == "init":
        overrides = {k: getattr(a, k) for k in ("stock_pct", "step_pct", "min_trade_pct", "confirm_days",
                                                "pair_confirm_days", "solo_after_days")
                     if getattr(a, k) is not None}
        cfg = {k: v for k, v in asdict(Config()).items() if k != "kr_taxable"} | overrides
        db.set_setting(conn, "config", cfg)
        today = now.tz_convert("Asia/Seoul").strftime("%Y-%m-%d")
        db.save_positions(conn, today, "US", {CASH: a.usd}, {})
        db.save_positions(conn, today, "KR", {CASH: a.krw}, {})
        print(f"paper account: USD {a.usd:,.2f} / KRW {a.krw:,.0f}; config {cfg}")
        return
    if a.cmd == "status":
        print(status(conn))
        return

    cfg, src = load_config(conn), make_source(a.source)
    if a.cmd == "tick":
        for line in tick(conn, src, cfg, now) or ["nothing due"]:
            print(line)
        return
    mkt = MARKETS[a.market]
    session = a.date
    if session is None:
        s = current_session(mkt, now)
        if s is None:
            raise SystemExit(f"{a.market} market is closed today; pass --date")
        session = s.session
    fn = run_signal if a.cmd == "signal" else run_execute
    try:
        print(fn(conn, mkt, session, src, cfg, now, force=a.force))
    except JobError as e:
        raise SystemExit(f"FAILED: {e}")


if __name__ == "__main__":
    main()
