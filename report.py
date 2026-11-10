"""Daily report text built from SQLite only (no brokerage calls)."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

import pandas as pd

NAMES = {"005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "373220.KS": "LG에너지솔루션",
         "207940.KS": "삼성바이오로직스", "005380.KS": "현대차", "148070.KS": "국고채10년",
         "402340.KS": "SK스퀘어", "114260.KS": "국고채3년", "385560.KS": "국고채30년",
         "SHY": "SHY(단기채)", "TLT": "TLT(장기채)"}
EVERY = {"day": "일간", "week": "주간", "month": "월간"}
NO_TRADE = "매매 주기 아님"  # execute detail on days outside the trade cadence; not a warning
FLAGS = {"KR": "🇰🇷", "US": "🇺🇸"}
WEEKDAYS = "월화수목금토일"


def name(ticker: str) -> str:
    return NAMES.get(ticker, ticker)


def money(v: float, market: str) -> str:
    return f"₩{v:,.0f}" if market == "KR" else f"${v:,.2f}"


def price(v: float, market: str) -> str:
    return f"{v:,.0f}" if market == "KR" else f"{v:,.2f}"


def mcap(v: float, market: str) -> str:
    return f"{v / 1e12:,.0f}조" if market == "KR" else f"${v / 1e12:,.2f}T"


def pct(v: float) -> str:
    return f"{v:+.2%}"


def _latest(conn: sqlite3.Connection, table: str, market: str, col: str = "session_date") -> str | None:
    return conn.execute(f"SELECT MAX({col}) FROM {table} WHERE market = ?", (market,)).fetchone()[0]


def market_section(conn: sqlite3.Connection, market: str) -> tuple[list[str], float | None, list[str]]:
    """(lines, pocket value in local currency, warnings)."""
    pos_date = _latest(conn, "positions", market, "date")
    if pos_date is None:
        return [f"{FLAGS[market]} {market} 기록 없음"], None, []
    pos = conn.execute("SELECT ticker, qty, value FROM positions WHERE market = ? AND date = ?",
                       (market, pos_date)).fetchall()
    value = sum(r["value"] for r in pos)

    head = f"{FLAGS[market]} {market} {pos_date}"
    from jobs import load_config  # local: jobs imports this module

    st = conn.execute("SELECT * FROM rank_state WHERE market = ? ORDER BY session_date DESC LIMIT 1",
                      (market,)).fetchone()
    if st:
        cfg = load_config(conn)
        ranking = " > ".join(name(t) for t in json.loads(st["confirmed"])[:3])
        head += (f" | 순위 {ranking} | 다음 매매 {EVERY.get(cfg.trade_every, cfg.trade_every)}"
                 f" | 대기 {st['streak']}/{cfg.confirm_days}일")
    lines = [head, f"  평가 {money(value, market)}"]

    tgt_session = _latest(conn, "targets", market)
    targets = {r["ticker"]: r["weight"] for r in conn.execute(
        "SELECT ticker, weight FROM targets WHERE market = ? AND session_date = ?", (market, tgt_session))}
    held = sorted((r for r in pos if r["ticker"] != "CASH"), key=lambda r: -r["value"])
    parts = [f"{name(r['ticker'])} {r['value'] / value:.1%}" + (f"(목표 {targets[r['ticker']]:.0%})"
                                                                if r["ticker"] in targets else "")
             for r in held if value]
    cash = next((r["value"] for r in pos if r["ticker"] == "CASH"), 0.0)
    parts.append(f"현금 {cash / value:.1%}" if value else "현금 0")
    lines.append("  보유 " + ", ".join(parts))

    ord_session = _latest(conn, "orders", market)
    if ord_session:
        agg: dict[tuple[str, str], list] = defaultdict(lambda: [0, 0.0, 0, set()])  # filled, notional, qty, statuses
        for r in conn.execute("SELECT * FROM orders WHERE market = ? AND session_date = ? ORDER BY id",
                              (market, ord_session)):
            a = agg[(r["ticker"], r["side"])]
            a[0] += r["filled_qty"]
            a[1] += r["filled_qty"] * (r["fill_price"] or 0)
            a[2] = max(a[2], r["qty"])
            a[3].add(r["status"])
        trades = []
        for (t, side), (filled, notional, wanted, statuses) in agg.items():
            verb = "매수" if side == "BUY" else "매도"
            if statuses == {"PLANNED"}:
                trades.append(f"{verb} {name(t)} {wanted}주 계획")
            elif filled:
                trades.append(f"{verb} {name(t)} {filled}주 @{price(notional / filled, market)}"
                              + (f" ({filled}/{wanted})" if filled < wanted else ""))
            else:
                trades.append(f"{verb} {name(t)} 미체결")
        lines.append(f"  체결({ord_session}) " + " / ".join(trades))
    else:
        lines.append("  체결 없음")

    rk_session = _latest(conn, "rankings", market)
    ranks = conn.execute("SELECT ticker, mcap FROM rankings WHERE market = ? AND session_date = ? ORDER BY rank "
                         "LIMIT 3", (market, rk_session)).fetchall()
    if ranks:
        txt = " > ".join(f"{name(r['ticker'])} {mcap(r['mcap'], market)}" for r in ranks)
        if len(ranks) > 1:
            txt += f" (1·2위 차 {ranks[0]['mcap'] / ranks[1]['mcap'] - 1:+.1%})"
        lines.append("  순위 " + txt)

    warns = [f"{market} {r['job']} {r['session_date']} {r['status']}: {(r['detail'] or '')[:120]}"
             for r in conn.execute("SELECT * FROM runs WHERE market = ? AND status IN ('FAILED','SKIPPED','PARTIAL',"
                                   "'RUNNING') AND session_date >= ? AND COALESCE(detail, '') != ? ORDER BY session_date, job",
                                   (market, pos_date, NO_TRADE))]
    return lines, value, warns


def fetch_usdkrw() -> float:
    import yfinance as yf
    close = yf.download("KRW=X", period="5d", interval="1d", progress=False, auto_adjust=False)["Close"]
    return float(close.squeeze().dropna().iloc[-1])


def pocket_values(conn: sqlite3.Connection) -> tuple[float | None, float | None]:
    """Latest snapshot value of the KR (KRW) and US (USD) pockets."""
    out = []
    for m in ("KR", "US"):
        d = _latest(conn, "positions", m, "date")
        out.append(None if d is None else conn.execute(
            "SELECT SUM(value) FROM positions WHERE market = ? AND date = ?", (m, d)).fetchone()[0])
    return out[0], out[1]


def pocket_cash(conn: sqlite3.Connection, market: str) -> float | None:
    """Cash in the latest positions snapshot of one pocket (local currency)."""
    d = _latest(conn, "positions", market, "date")
    if d is None:
        return None
    row = conn.execute("SELECT value FROM positions WHERE market = ? AND date = ? AND ticker = 'CASH'",
                       (market, d)).fetchone()
    return row[0] if row else 0.0


def build_report(conn: sqlite3.Connection, day: str, usdkrw: float | None,
                 last: dict | None = None, first: dict | None = None,
                 extra: list[str] | None = None) -> tuple[str, float | None]:
    """(text, total in KRW or None without an FX rate). last/first: earlier reports' {date, total};
    extra: lines (e.g. the FX plan) shown between the markets and the warnings."""
    wd = WEEKDAYS[pd.Timestamp(day).dayofweek]
    kr_lines, kr_val, kr_warn = market_section(conn, "KR")
    us_lines, us_val, us_warn = market_section(conn, "US")

    total = None
    if usdkrw and kr_val is not None and us_val is not None:
        total = kr_val + us_val * usdkrw
    out = [f"📊 일일 보고 {day} ({wd})"]
    if total is not None:
        chg = []
        if last and last.get("total"):
            chg.append(f"직전 {pct(total / last['total'] - 1)}")
        if first and first.get("total"):
            chg.append(f"누적 {pct(total / first['total'] - 1)}")
        out.append(f"총자산 ₩{total:,.0f}" + (f" ({', '.join(chg)})" if chg else ""))
        out.append(f"  원화 {money(kr_val, 'KR')} · 달러 {money(us_val, 'US')} (환율 {usdkrw:,.1f})")
    else:
        out.append("총자산 환율 조회 실패 — 원화/달러 따로 표시")
    out += ["", *kr_lines, "", *us_lines]
    if extra:
        out += ["", *extra]
    warns = kr_warn + us_warn
    out += ["", "⚠️ 확인 필요", *(f"  {w}" for w in warns)] if warns else ["", "✅ 이상 없음"]
    return "\n".join(out), total
