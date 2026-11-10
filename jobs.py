"""Daily routine CLI (phase 1: yfinance data, paper account in SQLite).

    python jobs.py init --usd 35000 --krw 50000000 [--stock-pct 60]
    python jobs.py signal --market KR [--date 2026-10-27] [--force]   # 시총 조회 -> 순위 -> 목표 비중
    python jobs.py execute --market KR [--date 2026-10-27] [--force]  # 목표 비중대로 모의 체결
    python jobs.py tick                                               # 지금 할 일 실행 (스케줄러용)
    python jobs.py status
    python jobs.py report [--print]                                   # 일일 보고 (tick 이 매일 08:00 KST 전송)
    python jobs.py web [--serve]                                      # 웹 리포트 site/index.html, 로컬 서버
    python jobs.py config --broker kis [--submit] [--allow-real]      # 브로커 선택 (기본 sim, kis 는 DRY_RUN)
"""
from __future__ import annotations

import argparse
import errno
import logging
import math
import sqlite3
import sys
import time
from dataclasses import asdict, fields, replace
from datetime import date as dt_date, time as dtime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

import db
import control
import notify
import report
import webreport
from brokers import Broker, SimBroker
from execution import ExecParams, execute, limit_price
from markets import MARKETS, Market, current_session, session_times
from sources import FallbackSource, Quote, Source, YFinanceSource
from strategy import (CASH, Config, RankState, plan_moves, pocket_value, pocket_weights, stock_weights, trade_cost,
                      trade_due, update_ranking)

log = logging.getLogger("mcr")

MAX_QUOTE_AGE = timedelta(minutes=40)  # yfinance KRX quotes lag ~20 min
SHARES_JUMP = 0.20                     # reject a new share count that moves more than this vs the last one
MCAP_JUMP = 0.40                       # day-over-day market-cap move treated as bad data


NO_TRADE = report.NO_TRADE  # execute detail on days outside the trade cadence (not worth an alert)


class JobError(RuntimeError):
    pass


def load_config(conn: sqlite3.Connection) -> Config:
    known = {f.name for f in fields(Config)} - {"kr_taxable"}
    saved: dict[str, Any] = {k: tuple(v) if isinstance(v, list) else v
             for k, v in db.get_setting(conn, "config", {}).items() if k in known}
    cfg = Config(**saved)
    retired = db.get_setting(conn, "retired", {}).get("KR", {})
    return replace(cfg, kr_taxable=frozenset(cfg.kr_universe) | {t for t, role in retired.items() if role == "stock"})


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


def retired_of(conn: sqlite3.Connection, mkt: Market) -> dict[str, str]:
    """Tickers dropped from the candidate/bond lists that are still being sold down: {ticker: stock|bond}."""
    return db.get_setting(conn, "retired", {}).get(mkt.code, {})


def refresh_shares(conn: sqlite3.Connection, mkt: Market, session: str, src: Source,
                   cfg: Config) -> dict[str, float]:
    universe = list(cfg.universe(mkt.code))
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


def _guarded(job: str, conn: sqlite3.Connection, mkt: Market | str, session: str, force: bool, fn) -> str:
    code = mkt if isinstance(mkt, str) else mkt.code
    last = conn.execute("SELECT status, detail FROM runs WHERE job = ? AND market = ? AND session_date = ?",
                        (job, code, session)).fetchone()
    if not db.claim_run(conn, job, code, session, force):
        return f"{job} {code} {session}: already done"
    try:
        status, detail = fn()
    except Exception as e:
        db.finish_run(conn, job, code, session, "FAILED", repr(e))
        if isinstance(e, JobError):
            log.error("%s %s %s failed: %s", job, code, session, e)
        else:
            log.exception("%s %s %s failed", job, code, session)
        if not (last and last["status"] == "FAILED" and last["detail"] == repr(e)):  # tick retries every 5 min
            why = notify.hint(str(e))
            notify.send(f"❌ {job} {code} {session} FAILED: {e}" + (f"\n→ {why}" if why else ""))
        raise
    db.finish_run(conn, job, code, session, status, detail)
    log.info("%s %s %s %s: %s", job, code, session, status, detail)
    # alert on anything a human should look at: held-back signals, partial fills, and every trade
    if job != "report" and detail != NO_TRADE and (
            status in ("SKIPPED", "PARTIAL") or (job == "execute" and not detail.startswith("no orders"))):
        notify.send(f"{'⚠️' if status != 'OK' else '✅'} {job} {code} {session} {status}: {detail}")
    return f"{job} {code} {session} {status}: {detail}"


def run_signal(conn: sqlite3.Connection, mkt: Market, session: str, src: Source, cfg: Config,
               now: pd.Timestamp, force: bool = False, broker: Broker | None = None) -> str:
    """broker given: holdings come from the brokerage account (and are snapshotted); otherwise from SQLite.
    Every session ranks the candidates; only a session inside the trade cadence also plans targets."""
    def work():
        tz = session_times(mkt, session).open.tz
        universe, bonds = list(cfg.universe(mkt.code)), list(cfg.bonds(mkt.code))
        retired = retired_of(conn, mkt)
        units = managed(broker.balance(mkt.code) if broker else db.latest_positions(conn, mkt.code)[1],
                        mkt, cfg, retired)
        if not units:
            raise JobError("no positions; run `jobs.py init` first")
        held = [t for t in retired if units.get(t)]
        shares = refresh_shares(conn, mkt, session, src, cfg)
        quotes = fresh_quotes(src, universe + bonds + held, session, str(tz), now)
        no_bond = [t for t in bonds if t not in quotes]
        if no_bond:
            raise JobError(f"no fresh quote for bond ETF {no_bond}")

        prev = db.previous_mcaps(conn, mkt.code, session)
        rows, valid, suspect = [], {}, []
        for t in universe:
            q = quotes.get(t)
            if q is None or t not in shares:
                continue  # no data (e.g. not listed yet): ranked without it, weights renormalize
            mcap = q.price * shares[t]
            flag = None
            if t in prev and abs(mcap / prev[t] - 1) > MCAP_JUMP:
                flag = f"mcap jump {prev[t]:.3g} -> {mcap:.3g}"
                suspect.append(t)
            else:
                valid[t] = mcap
            rows.append({"ticker": t, "price": q.price, "quote_time": q.time.isoformat(), "shares": shares[t],
                         "mcap": mcap, "source": q.source or src.name, "flag": flag})
        rows += [{"ticker": t, "price": quotes[t].price, "quote_time": quotes[t].time.isoformat(),
                  "source": quotes[t].source or src.name} for t in bonds + held]
        db.save_snapshot(conn, session, mkt.code, rows)

        ranked = sorted(valid.items(), key=lambda kv: kv[1], reverse=True)
        db.save_rankings(conn, session, mkt.code, ranked)
        state = db.load_rank_state(conn, mkt.code, session)
        before = state.confirmed.copy()
        if suspect or len(ranked) < 2:
            # bad or missing data: keep yesterday's confirmed ranking
            if not before:
                raise JobError(f"not enough clean market caps on first run (suspect={suspect})")
            confirmed = [t for t in before if t in universe and t in quotes]
            note = f"HOLD (suspect={suspect}, ranked={len(ranked)})"
        else:
            confirmed, note = update_ranking(state, [t for t, _ in ranked], cfg), "OK"
        db.save_rank_state(conn, session, mkt.code, state)
        if before and note == "OK" and before != confirmed:
            notify.send(f"🔄 {mkt.code} {session} 순위 변화: {' > '.join(before)} → {' > '.join(confirmed)}")

        prices = {t: q.price for t, q in quotes.items()}
        missing = [t for t in units if t != CASH and t not in prices]
        if missing:
            raise JobError(f"no fresh quote for held {missing}")
        if broker:
            db.save_positions(conn, session, mkt.code, units, prices)
        value = pocket_value(units, prices)
        investable = value  # seam for the FX plan: cash to keep out of the market (Task 4)

        top = " > ".join(t for t, _ in ranked[:3])
        last = db.last_trade_date(conn, mkt.code, before=session)
        if not trade_due(dt_date.fromisoformat(session), dt_date.fromisoformat(last) if last else None,
                         cfg.trade_every):
            db.save_targets(conn, session, mkt.code, {})  # a forced re-run must not leave an earlier plan behind
            tail = f"no trade ({cfg.trade_every})"
        else:
            weights = pocket_weights(stock_weights(confirmed, cfg), mkt.code, cfg)
            target = plan_moves(units, prices, weights, cfg, investable)
            if value > 0:
                db.save_targets(conn, session, mkt.code, {t: (v / value, prices[t]) for t, v in target.items()})
                tail = "target " + ", ".join(f"{t} {v / value:.1%}"
                                             for t, v in sorted(target.items(), key=lambda kv: -kv[1]))
            else:
                db.save_targets(conn, session, mkt.code, {})
                tail = "target none (empty pocket)"
        done = [t for t in retired if not units.get(t)]
        if done:
            control.drop_retired(conn, mkt.code, done)
        return ("OK" if note == "OK" else "SKIPPED"), f"{note}; rank {top}; {tail}"

    return _guarded("signal", conn, mkt, session, force, work)


def managed(units: dict[str, float], mkt: Market, cfg: Config, retired: dict[str, str]) -> dict[str, float]:
    """Holdings this strategy may trade: candidates, the two bond ETFs, tickers being retired and cash.
    Anything else in the account (the user's own positions) is left untouched."""
    keep = set(cfg.universe(mkt.code)) | set(cfg.bonds(mkt.code)) | set(retired) | {CASH}
    other = sorted(t for t, q in units.items() if t not in keep and q)
    if other:
        log.warning("%s: ignoring holdings outside the strategy: %s", mkt.code, other)
    return {t: q for t, q in units.items() if t in keep}


def plan_orders(targets: dict[str, tuple[float, float]], units: dict[str, float], prices: dict[str, float],
                cfg: Config) -> list[tuple[str, int]]:
    """Integer-share deltas toward target weights; trades under min_trade_pct are skipped (full exits never)."""
    value = pocket_value(units, prices)
    min_trade = cfg.min_trade_pct / 100 * value
    orders = []
    for t in sorted((set(targets) | set(units)) - {CASH}):
        want = math.floor(targets.get(t, (0.0, 0.0))[0] * value / prices[t])
        delta = want - int(units.get(t, 0))
        if delta and (want == 0 or abs(delta) * prices[t] >= min_trade):
            orders.append((t, delta))
    return orders


def run_execute(conn: sqlite3.Connection, mkt: Market, session: str, src: Source, cfg: Config,
                now: pd.Timestamp, force: bool = False, broker: Broker | None = None, dry_run: bool = False,
                params: ExecParams = ExecParams(), sleep=time.sleep) -> str:
    """Trade toward the session's targets. broker=None simulates fills against the SQLite positions;
    dry_run records the planned orders without sending them."""
    def work():
        if db.run_status(conn, "signal", mkt.code, session) not in ("OK", "SKIPPED"):
            raise JobError("signal has not completed for this session")
        targets = db.load_targets(conn, session, mkt.code)
        if not targets:
            return "SKIPPED", NO_TRADE
        retired = retired_of(conn, mkt)
        units = managed(broker.balance(mkt.code) if broker else db.latest_positions(conn, mkt.code)[1],
                        mkt, cfg, retired)
        tickers = sorted((set(targets) | set(units)) - {CASH})
        tz = str(session_times(mkt, session).open.tz)
        quotes = fresh_quotes(src, tickers, session, tz, now)
        missing = [t for t in tickers if t not in quotes]
        if missing:
            raise JobError(f"no fresh quote for {missing}")
        prices = {t: q.price for t, q in quotes.items()}
        plan = plan_orders(targets, units, prices, cfg)
        etfs = set(cfg.bonds(mkt.code)) | {t for t, role in retired.items() if role == "bond"}

        if dry_run:
            for t, delta in plan:
                side = "BUY" if delta > 0 else "SELL"
                db.save_order(conn, {"session_date": session, "market": mkt.code, "ticker": t, "side": side,
                                     "step": 0, "qty": abs(delta), "ref_price": prices[t],
                                     "limit_price": limit_price(prices[t], 0.0, mkt.code, side, t in etfs),
                                     "status": "PLANNED"})
            return "OK", "DRY_RUN " + ("; ".join(f"{t} {d:+d}@{prices[t]:,.2f}" for t, d in plan) or "no orders")

        b = broker or SimBroker({mkt.code: units}, prices, cfg)
        results = execute(b, mkt.code, plan, prices, etfs=etfs, params=params, sleep=sleep)
        for r in results:
            for o in r.orders:
                fill_px = o["fill_price"] or o["limit_price"]
                signed = o["filled"] if r.side == "BUY" else -o["filled"]
                db.save_order(conn, {"session_date": session, "market": mkt.code, "ticker": r.ticker,
                                     "side": r.side, "step": o["step"], "qty": o["qty"],
                                     "ref_price": o["ref_price"], "limit_price": o["limit_price"],
                                     "filled_qty": o["filled"], "fill_price": o["fill_price"],
                                     "status": o["status"],
                                     "cost": trade_cost(cfg, mkt.code, r.ticker, signed * fill_px),
                                     "broker_order_no": o["broker_order_no"]})
        new_units = managed(b.balance(mkt.code), mkt, cfg, retired)
        db.save_positions(conn, session, mkt.code, new_units, prices)
        fills = "; ".join(f"{r.side} {r.ticker} {r.filled}/{r.wanted}@{r.avg_price or 0:,.2f}" for r in results)
        # buys capped by cash (fees above the model, fills up the ladder) are expected, not a failure
        capped = [r.ticker for r in results if r.filled < r.wanted and r.cash_limited]
        short = [r.ticker for r in results if r.filled < r.wanted and not r.cash_limited]
        detail = (fills or "no orders") + f"; value {pocket_value(new_units, prices):,.0f}"
        detail += (f"; cash-limited {capped}" if capped else "") + (f"; unfilled {short}" if short else "")
        return ("OK" if not short else "PARTIAL"), detail

    return _guarded("execute", conn, mkt, session, force, work)


REPORT_AT = dtime(8, 0)  # KST: before the KR session, after the US session has closed
SITE_DIR = Path(__file__).with_name("site")


def run_report(conn: sqlite3.Connection, now: pd.Timestamp, fx=report.fetch_usdkrw, force: bool = False,
               send: bool = True) -> str:
    """Daily report over the latest SQLite state; skipped when no job ran since the previous report."""
    day = now.tz_convert("Asia/Seoul").strftime("%Y-%m-%d")

    def work():
        last, first = db.get_setting(conn, "report:last"), db.get_setting(conn, "report:first")
        # rowid, not timestamps: claim_run's INSERT OR REPLACE gives every (re)started job a new, larger rowid
        seen = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM runs WHERE job != 'report'").fetchone()[0]
        if last and seen <= last.get("runs_rowid", 0) and not force:
            return "SKIPPED", "no jobs ran since the last report"
        try:
            usdkrw = fx()
        except Exception as e:  # report without a KRW total rather than not at all
            log.warning("USD/KRW fetch failed: %s", e)
            usdkrw = None
        text, total = report.build_report(conn, day, usdkrw, last, first)
        kr_val, us_val = report.pocket_values(conn)
        if usdkrw and kr_val is not None and us_val is not None:
            db.save_equity(conn, day, kr_val, us_val, usdkrw)
        if not send:
            print(text)
            return "OK", "printed"
        ok = notify.send(text)
        try:
            webreport.write_site(conn, SITE_DIR, usdkrw)
        except Exception:  # the web page is a convenience; never fail the report over it
            log.exception("web report write failed")
        if total is not None:
            entry = {"date": day, "total": total, "runs_rowid": seen}
            db.set_setting(conn, "report:last", entry)
            if not (first and first.get("total")):  # a total-0 report (empty account) is no 누적 baseline
                db.set_setting(conn, "report:first", entry)
        return "OK", ("sent" if ok else "not sent (telegram off or failed)") +             (f"; total {total:,.0f}" if total is not None else "")

    if not send:
        status, detail = work()
        return f"report {day} {status}: {detail}"
    return _guarded("report", conn, "ALL", day, force, work)


def tick(conn: sqlite3.Connection, src: Source, cfg: Config, now: pd.Timestamp,
         broker: Broker | None = None, dry_run: bool = False, fx=report.fetch_usdkrw) -> list[str]:
    """Run whatever is due now. Safe to call every few minutes; runs table prevents repeats."""
    out = []
    paused = control.paused(conn)
    for mkt in MARKETS.values():
        s = current_session(mkt, now)
        if s is None or now >= s.close:
            continue
        if paused[mkt.code]:
            out.append(f"{mkt.code} paused")
            continue
        try:
            if now >= s.signal and db.run_status(conn, "signal", mkt.code, s.session) not in ("OK", "SKIPPED"):
                out.append(run_signal(conn, mkt, s.session, src, cfg, now, broker=broker))
            if now >= s.execute and now < s.close - timedelta(minutes=15) \
                    and db.run_status(conn, "execute", mkt.code, s.session) not in ("OK", "PARTIAL", "SKIPPED"):
                out.append(run_execute(conn, mkt, s.session, src, cfg, now, broker=broker, dry_run=dry_run))
        except Exception as e:  # keep the other market going
            out.append(f"{mkt.code} {s.session} FAILED: {e!r}")
    kst = now.tz_convert("Asia/Seoul")
    if kst.time() >= REPORT_AT and db.run_status(conn, "report", "ALL", kst.strftime("%Y-%m-%d")) not in ("OK", "SKIPPED"):
        try:
            out.append(run_report(conn, now, fx=fx))
        except Exception as e:
            out.append(f"report FAILED: {e!r}")
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


def make_broker(conn: sqlite3.Connection) -> tuple[Broker | None, bool]:
    """(broker, dry_run). sim -> (None, False): fills simulated against SQLite positions.
    kis -> orders are only recorded (DRY_RUN) unless `submit`; a real-money account additionally
    needs `allow_real`, so switching .env to a real key never trades by itself."""
    t = control.trading_settings(conn)
    if t["broker"] == "sim":
        return None, False
    from kis_api import KisClient, KisSource
    from kis_broker import KisBroker
    client = KisClient()
    if client.creds.env == "real" and not t["allow_real"]:
        raise JobError("KIS_ENV=real but real trading is not enabled (jobs.py config --allow-real)")
    broker = KisBroker(client, exchange_of=KisSource(client, us_shares=YFinanceSource()).exchange)
    dry_run = not t["submit"]
    if not dry_run and client.creds.env == "real":
        log.warning("REAL-MONEY ORDERS ENABLED")
    return broker, dry_run


def status(conn: sqlite3.Connection) -> str:
    t = control.trading_settings(conn)
    paused = control.paused(conn)
    lines = [f"broker {t['broker']} submit={t['submit']} allow_real={t['allow_real']}",
             f"paused KR={paused['KR']} US={paused['US']}"]
    for code in MARKETS:
        lines.append(f"[{code}]")
        for r in conn.execute("SELECT * FROM runs WHERE market = ? ORDER BY session_date DESC, job DESC LIMIT 4",
                              (code,)):
            lines.append(f"  run {r['session_date']} {r['job']:7s} {r['status']:7s} {r['detail'] or ''}")
        date, units = db.latest_positions(conn, code)
        lines.append(f"  positions@{date}: " + ", ".join(f"{t} {q:,.2f}" for t, q in units.items()))
    return "\n".join(lines)


def serve_web(app: Any, hosts: list[str], port: int) -> None:
    """Serve the web app on every host at the same port: one uvicorn server, one socket per host."""
    import uvicorn

    configs = [uvicorn.Config(app, host=h, port=port, log_level="warning") for h in dict.fromkeys(hosts)]
    sockets = []
    for c in configs:
        try:
            sockets.append(c.bind_socket())
        except SystemExit as e:  # uvicorn logs the bind OSError and exits
            err = e.__context__
            if isinstance(err, OSError):
                hint = (f"already running? open http://{c.host}:{port}/" if err.errno == errno.EADDRINUSE
                        else "is this address up on this machine?")
                raise SystemExit(f"cannot listen on {c.host}:{port} ({err.strerror}); {hint}") from None
            raise
    uvicorn.Server(configs[0]).run(sockets=sockets)


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
    p = sub.add_parser("web", help="investment report web page (site/index.html; --serve for a local server)")
    p.add_argument("--serve", action="store_true")
    p.add_argument("--host", action="append",
                   help="address to listen on, repeatable (default 127.0.0.1 = this PC only; no login on the page)")
    p.add_argument("--port", type=int, default=8765)
    p = sub.add_parser("report", help="daily report (sent to Telegram; --print to only show it)")
    p.add_argument("--print", dest="print_only", action="store_true")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("config", help="choose broker and order submission")
    p.add_argument("--broker", choices=["sim", "kis"])
    p.add_argument("--submit", action=argparse.BooleanOptionalAction,
                   help="kis: actually send orders (default: record only, DRY_RUN)")
    p.add_argument("--allow-real", action=argparse.BooleanOptionalAction,
                   help="permit orders on a KIS_ENV=real account")
    pause_group = p.add_mutually_exclusive_group()
    pause_group.add_argument("--pause", choices=["KR", "US", "ALL"])
    pause_group.add_argument("--resume", choices=["KR", "US", "ALL"])
    a = ap.parse_args()

    logs = Path(__file__).with_name("logs")
    logs.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(logs / "jobs.log", encoding="utf-8")]
    if sys.stderr is not None:  # None under pythonw (scheduled task)
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    conn = db.connect(a.db)
    control.migrate(conn)
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
    if a.cmd == "web":
        print(f"wrote {webreport.write_site(conn, SITE_DIR)}")
        if a.serve:
            import api

            serve_web(api.create_app(a.db, api.load_token()), a.host or ["127.0.0.1"], a.port)
        return
    if a.cmd == "report":
        print(run_report(conn, now, force=a.force or a.print_only, send=not a.print_only))
        return
    if a.cmd == "config":
        changes = {k: v for k, v in (("broker", a.broker), ("submit", a.submit),
                                     ("allow_real", a.allow_real)) if v is not None}
        updates = {"trading": changes}
        market = a.pause or a.resume
        if market:
            codes = ("KR", "US") if market == "ALL" else (market,)
            updates["paused"] = {code: bool(a.pause) for code in codes}
        t = control.update(conn, updates, "cli")["trading"]
        print(f"trading settings: {t}")
        return

    cfg, src = load_config(conn), make_source(a.source)
    try:
        broker, dry_run = make_broker(conn)
    except JobError as e:
        today = now.tz_convert("Asia/Seoul").strftime("%Y-%m-%d")
        if db.get_setting(conn, "alerted:broker") != [today, str(e)]:  # once a day, not every tick
            notify.send(f"❌ {a.cmd} FAILED: {e}")
            db.set_setting(conn, "alerted:broker", [today, str(e)])
        raise SystemExit(f"FAILED: {e}")
    if a.cmd == "tick":
        for line in tick(conn, src, cfg, now, broker=broker, dry_run=dry_run) or ["nothing due"]:
            print(line)
        return
    mkt = MARKETS[a.market]
    session = a.date
    if session is None:
        s = current_session(mkt, now)
        if s is None:
            raise SystemExit(f"{a.market} market is closed today; pass --date")
        session = s.session
    try:
        if a.cmd == "signal":
            print(run_signal(conn, mkt, session, src, cfg, now, force=a.force, broker=broker))
        else:
            print(run_execute(conn, mkt, session, src, cfg, now, force=a.force, broker=broker, dry_run=dry_run))
    except JobError as e:
        raise SystemExit(f"FAILED: {e}")


if __name__ == "__main__":
    main()
