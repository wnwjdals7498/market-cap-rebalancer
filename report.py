"""Daily report text built from SQLite only (no brokerage calls)."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict

import pandas as pd

import control
import db

NAMES = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "402340.KS": "SK스퀘어",
    "373220.KS": "LG에너지솔루션", "005380.KS": "현대차", "207940.KS": "삼성바이오로직스",
    "009150.KS": "삼성전기", "105560.KS": "KB금융", "068270.KS": "셀트리온",
    "028260.KS": "삼성물산", "032830.KS": "삼성생명", "055550.KS": "신한지주",
    "000270.KS": "기아", "006400.KS": "삼성SDI", "012450.KS": "한화에어로스페이스",
    "114260.KS": "KODEX 국고채3년", "385560.KS": "RISE 국고채30년", "411060.KS": "ACE KRX금현물",
    "069500.KS": "KODEX 200", "229200.KS": "KODEX 코스닥150", "148070.KS": "국고채10년",
    "AAPL": "애플", "NVDA": "엔비디아", "MSFT": "마이크로소프트", "GOOG": "알파벳",
    "AMZN": "아마존", "TSM": "TSMC", "SPCX": "스페이스X", "META": "메타", "AVGO": "브로드컴",
    "TSLA": "테슬라", "LLY": "일라이릴리", "BRK-B": "버크셔해서웨이", "WMT": "월마트",
    "SKHY": "SK하이닉스 미국상장", "JPM": "JP모건", "SHY": "미국 단기국채", "TLT": "미국 장기국채",
    "IEF": "미국 중기국채", "GLD": "금 ETF", "QQQ": "나스닥100 ETF", "SPY": "S&P500 ETF",
}
EVERY = {"day": "일간", "week": "주간", "month": "월간"}
NO_TRADE = "매매 주기 아님"  # execute detail on days outside the trade cadence; not a warning
FLAGS = {"KR": "🇰🇷", "US": "🇺🇸"}
WEEKDAYS = "월화수목금토일"


def name(ticker: str, saved: dict[str, str] | None = None) -> str:
    label = NAMES.get(ticker) or (saved or {}).get(ticker)
    if not label or label == ticker:
        return ticker
    return label if ticker.endswith((".KS", ".KQ")) else f"{label}({ticker})"


def display_names(conn: sqlite3.Connection) -> dict[str, str]:
    saved = db.get_setting(conn, "names", {})
    return {ticker: name(ticker, saved) for ticker in NAMES.keys() | saved.keys()}


def replace_tickers(text: str, names: dict[str, str]) -> str:
    if not names:
        return text
    pattern = r"(?<![A-Za-z0-9.\-])(?:" + "|".join(re.escape(t) for t in sorted(names, key=len, reverse=True)) + r")(?![A-Za-z0-9.\-])"
    return re.sub(pattern, lambda match: names[match.group()], text)


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
    names = db.get_setting(conn, "names", {})
    pos_date = _latest(conn, "positions", market, "date")
    if pos_date is None:
        return [f"{FLAGS[market]} {market} 기록 없음"], None, []
    pos = conn.execute("SELECT ticker, qty, value FROM positions WHERE market = ? AND date = ?",
                       (market, pos_date)).fetchall()
    value = sum(r["value"] for r in pos)

    head = f"{FLAGS[market]} {market} {pos_date}"
    st = conn.execute("SELECT * FROM rank_state WHERE market = ? ORDER BY session_date DESC LIMIT 1",
                      (market,)).fetchone()
    if st:
        cfg = control.load_config(conn)
        ranking = " > ".join(name(t, names) for t in json.loads(st["confirmed"])[:3])
        head += (f" | 순위 {ranking} | 다음 매매 {EVERY.get(cfg.trade_every, cfg.trade_every)}"
                 f" | 대기 {st['streak']}/{cfg.confirm_days}일")
    lines = [head, f"  평가 {money(value, market)}"]

    tgt_session = _latest(conn, "targets", market)
    targets = {r["ticker"]: r["weight"] for r in conn.execute(
        "SELECT ticker, weight FROM targets WHERE market = ? AND session_date = ?", (market, tgt_session))}
    held = sorted((r for r in pos if r["ticker"] != "CASH"), key=lambda r: -r["value"])
    parts = [f"{name(r['ticker'], names)} {r['value'] / value:.1%}" + (f"(목표 {targets[r['ticker']]:.0%})"
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
                trades.append(f"{verb} {name(t, names)} {wanted}주 계획")
            elif filled:
                trades.append(f"{verb} {name(t, names)} {filled}주 @{price(notional / filled, market)}"
                              + (f" ({filled}/{wanted})" if filled < wanted else ""))
            else:
                trades.append(f"{verb} {name(t, names)} 미체결")
        lines.append(f"  체결({ord_session}) " + " / ".join(trades))
    else:
        lines.append("  체결 없음")

    rk_session = _latest(conn, "rankings", market)
    ranks = conn.execute("SELECT ticker, mcap FROM rankings WHERE market = ? AND session_date = ? ORDER BY rank "
                         "LIMIT 3", (market, rk_session)).fetchall()
    if ranks:
        txt = " > ".join(f"{name(r['ticker'], names)} {mcap(r['mcap'], market)}" for r in ranks)
        if len(ranks) > 1:
            txt += f" (1·2위 차 {ranks[0]['mcap'] / ranks[1]['mcap'] - 1:+.1%})"
        lines.append("  순위 " + txt)

    warns = [f"{market} {r['job']} {r['session_date']} {r['status']}: {(r['detail'] or '')[:120]}"
             for r in conn.execute("SELECT * FROM runs WHERE market = ? AND status IN ('FAILED','SKIPPED','PARTIAL',"
                                   "'RUNNING') AND session_date >= ? AND COALESCE(detail, '') != ? ORDER BY session_date, job",
                                   (market, pos_date, NO_TRADE))]
    return lines, value, [replace_tickers(w, display_names(conn)) for w in warns]


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
