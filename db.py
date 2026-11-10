"""SQLite storage for the daily routine."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from strategy import LeaderState, RankState

DEFAULT_DB = Path(__file__).parent / "mcr.db"
# A RUNNING row older than this belongs to a process that died (PC slept, task killed). Longer than
# any job: execute's price ladder takes at most ~30 min; the scheduled task is capped at 45 min.
STALE_RUN = timedelta(minutes=60)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                 -- JSON
);
CREATE TABLE IF NOT EXISTS settings_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    key TEXT NOT NULL,
    old TEXT,
    new TEXT,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shares (     -- shares outstanding, refreshed once per day
    date   TEXT NOT NULL,
    ticker TEXT NOT NULL,
    shares REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (date, ticker)
);
CREATE TABLE IF NOT EXISTS snapshots (  -- intraday quote x shares taken by the signal job
    session_date TEXT NOT NULL,
    market       TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    price        REAL NOT NULL,
    quote_time   TEXT NOT NULL,
    shares       REAL,
    mcap         REAL,
    source       TEXT NOT NULL,
    flag         TEXT,                  -- validation warning, NULL if clean
    PRIMARY KEY (session_date, market, ticker)
);
CREATE TABLE IF NOT EXISTS rankings (
    session_date TEXT NOT NULL,
    market       TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    ticker       TEXT NOT NULL,
    mcap         REAL NOT NULL,
    PRIMARY KEY (session_date, market, rank)
);
CREATE TABLE IF NOT EXISTS leader_state (
    session_date TEXT NOT NULL,
    market       TEXT NOT NULL,
    mode         TEXT NOT NULL,
    holding      TEXT,
    leader       TEXT,
    streak       INTEGER NOT NULL,
    pair         TEXT NOT NULL,         -- JSON list
    candidate    TEXT,
    cand_streak  INTEGER NOT NULL,
    switches     INTEGER NOT NULL,
    PRIMARY KEY (session_date, market)
);
CREATE TABLE IF NOT EXISTS rank_state (
    session_date TEXT NOT NULL,
    market       TEXT NOT NULL,
    confirmed    TEXT NOT NULL,         -- JSON list, best first
    pending      TEXT NOT NULL,         -- JSON list
    streak       INTEGER NOT NULL,
    PRIMARY KEY (session_date, market)
);
CREATE TABLE IF NOT EXISTS targets (    -- weights within the market pocket
    session_date TEXT NOT NULL,
    market       TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    weight       REAL NOT NULL,
    ref_price    REAL NOT NULL,
    PRIMARY KEY (session_date, market, ticker)
);
CREATE TABLE IF NOT EXISTS positions (  -- holdings snapshot; CASH row has price 1
    date   TEXT NOT NULL,
    market TEXT NOT NULL,
    ticker TEXT NOT NULL,
    qty    REAL NOT NULL,
    price  REAL NOT NULL,
    value  REAL NOT NULL,
    PRIMARY KEY (date, market, ticker)
);
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date    TEXT NOT NULL,
    market          TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL,      -- BUY | SELL
    step            INTEGER NOT NULL DEFAULT 0,  -- price-ladder rung; one row per order sent
    qty             INTEGER NOT NULL,
    ref_price       REAL NOT NULL,
    limit_price     REAL NOT NULL,
    filled_qty      INTEGER NOT NULL DEFAULT 0,
    fill_price      REAL,
    cost            REAL,
    status          TEXT NOT NULL,      -- PLANNED | SUBMITTED | PARTIAL | FILLED | CANCELLED | REJECTED
    broker_order_no TEXT,
    created_at      TEXT NOT NULL,
    error           TEXT,
    UNIQUE (session_date, market, ticker, side, step)
);
CREATE TABLE IF NOT EXISTS equity (    -- daily totals written by the report job (history for charts)
    date      TEXT PRIMARY KEY,         -- KST date of the report
    kr_value  REAL NOT NULL,            -- KRW pocket
    us_value  REAL NOT NULL,            -- USD pocket
    usdkrw    REAL NOT NULL,
    total     REAL NOT NULL             -- KRW
);
CREATE TABLE IF NOT EXISTS runs (
    job          TEXT NOT NULL,         -- signal | execute
    market       TEXT NOT NULL,
    session_date TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,         -- RUNNING | OK | SKIPPED | FAILED
    detail       TEXT,
    PRIMARY KEY (job, market, session_date)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# --- settings ---------------------------------------------------------------

def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))


def log_setting_change(conn: sqlite3.Connection, key: str, old, new, source: str) -> None:
    conn.execute("INSERT INTO settings_log (at, key, old, new, source) VALUES (?, ?, ?, ?, ?)",
                 (now_iso(), key, json.dumps(old), json.dumps(new), source))


def setting_changes(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT * FROM settings_log ORDER BY id DESC LIMIT ?", (limit,))]


# --- runs (idempotency) -----------------------------------------------------

def claim_run(conn: sqlite3.Connection, job: str, market: str, session_date: str, force: bool = False,
              now: datetime | None = None) -> bool:
    """Marks a job RUNNING. False if it already finished (OK/SKIPPED/PARTIAL) or is still running for
    that session. FAILED runs and RUNNING rows older than STALE_RUN may be claimed again."""
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT status, started_at FROM runs WHERE job = ? AND market = ? AND session_date = ?",
                       (job, market, session_date)).fetchone()
    if row and not force:
        if row["status"] == "RUNNING":
            if now - datetime.fromisoformat(row["started_at"]) < STALE_RUN:
                return False
        elif row["status"] != "FAILED":
            return False
    with conn:
        conn.execute("INSERT OR REPLACE INTO runs (job, market, session_date, started_at, status) "
                     "VALUES (?, ?, ?, ?, 'RUNNING')",
                     (job, market, session_date, now.isoformat(timespec="seconds")))
    return True


def finish_run(conn: sqlite3.Connection, job: str, market: str, session_date: str, status: str,
               detail: str | None = None) -> None:
    with conn:
        conn.execute("UPDATE runs SET finished_at = ?, status = ?, detail = ? "
                     "WHERE job = ? AND market = ? AND session_date = ?",
                     (now_iso(), status, detail, job, market, session_date))


def run_status(conn: sqlite3.Connection, job: str, market: str, session_date: str) -> str | None:
    row = conn.execute("SELECT status FROM runs WHERE job = ? AND market = ? AND session_date = ?",
                       (job, market, session_date)).fetchone()
    return row["status"] if row else None


# --- shares / snapshots / rankings -----------------------------------------

def save_shares(conn: sqlite3.Connection, date: str, shares: dict[str, float], source: str) -> None:
    with conn:
        conn.executemany("INSERT OR REPLACE INTO shares VALUES (?, ?, ?, ?)",
                         [(date, t, s, source) for t, s in shares.items()])


def latest_shares(conn: sqlite3.Connection, tickers: list[str], before: str | None = None,
                  source: str | None = None) -> dict[str, tuple[str, float]]:
    """{ticker: (date, shares)} most recent on/before `before` (inclusive), optionally from one source
    (sources count shares differently, e.g. common only vs all classes)."""
    out = {}
    for t in tickers:
        row = conn.execute("SELECT date, shares FROM shares WHERE ticker = ? AND date <= ? "
                           "AND (? IS NULL OR source = ?) ORDER BY date DESC LIMIT 1",
                           (t, before or "9999-12-31", source, source)).fetchone()
        if row:
            out[t] = (row["date"], row["shares"])
    return out


def save_snapshot(conn: sqlite3.Connection, session_date: str, market: str, rows: list[dict]) -> None:
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO snapshots VALUES (:session_date, :market, :ticker, :price, :quote_time, "
            ":shares, :mcap, :source, :flag)",
            [{"session_date": session_date, "market": market, "shares": None, "mcap": None, "flag": None, **r}
             for r in rows])


def previous_mcaps(conn: sqlite3.Connection, market: str, before: str) -> dict[str, float]:
    """Market caps from the last snapshot session strictly before `before`."""
    row = conn.execute("SELECT MAX(session_date) AS d FROM snapshots WHERE market = ? AND session_date < ?",
                       (market, before)).fetchone()
    if not row["d"]:
        return {}
    return {r["ticker"]: r["mcap"] for r in conn.execute(
        "SELECT ticker, mcap FROM snapshots WHERE market = ? AND session_date = ? AND mcap IS NOT NULL",
        (market, row["d"]))}


def save_rankings(conn: sqlite3.Connection, session_date: str, market: str, ranked: list[tuple[str, float]]) -> None:
    with conn:
        conn.execute("DELETE FROM rankings WHERE session_date = ? AND market = ?", (session_date, market))
        conn.executemany("INSERT INTO rankings VALUES (?, ?, ?, ?, ?)",
                         [(session_date, market, i + 1, t, m) for i, (t, m) in enumerate(ranked)])


# --- leader state -----------------------------------------------------------

def load_state(conn: sqlite3.Connection, market: str, before: str) -> LeaderState:
    """State after the last session strictly before `before` (fresh state if none)."""
    row = conn.execute("SELECT * FROM leader_state WHERE market = ? AND session_date < ? "
                       "ORDER BY session_date DESC LIMIT 1", (market, before)).fetchone()
    if not row:
        return LeaderState()
    return LeaderState(mode=row["mode"], holding=row["holding"], leader=row["leader"], streak=row["streak"],
                       pair=json.loads(row["pair"]), candidate=row["candidate"], cand_streak=row["cand_streak"],
                       switches=row["switches"])


def save_state(conn: sqlite3.Connection, session_date: str, market: str, state: LeaderState) -> None:
    s = asdict(state)
    s["pair"] = json.dumps(s["pair"])
    with conn:
        conn.execute("INSERT OR REPLACE INTO leader_state VALUES (:session_date, :market, :mode, :holding, "
                     ":leader, :streak, :pair, :candidate, :cand_streak, :switches)",
                     {"session_date": session_date, "market": market, **s})


# --- rank state -------------------------------------------------------------

def load_rank_state(conn: sqlite3.Connection, market: str, before: str) -> RankState:
    """Ranking memory after the last session strictly before `before` (empty state if none)."""
    row = conn.execute("SELECT * FROM rank_state WHERE market = ? AND session_date < ? "
                       "ORDER BY session_date DESC LIMIT 1", (market, before)).fetchone()
    if not row:
        return RankState()
    return RankState(confirmed=json.loads(row["confirmed"]), pending=json.loads(row["pending"]),
                     streak=row["streak"])


def save_rank_state(conn: sqlite3.Connection, session_date: str, market: str, state: RankState) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO rank_state VALUES (?, ?, ?, ?, ?)",
                     (session_date, market, json.dumps(state.confirmed), json.dumps(state.pending), state.streak))


def last_trade_date(conn: sqlite3.Connection, market: str, before: str | None = None) -> str | None:
    """Latest session whose execute finished OK (strictly before `before` when given)."""
    row = conn.execute("SELECT MAX(session_date) FROM runs WHERE job = 'execute' AND market = ? AND status = 'OK' "
                       "AND (? IS NULL OR session_date < ?)", (market, before, before)).fetchone()
    return row[0]


# --- targets / positions / orders ------------------------------------------

def save_targets(conn: sqlite3.Connection, session_date: str, market: str,
                 targets: dict[str, tuple[float, float]]) -> None:
    """targets: {ticker: (weight, ref_price)}"""
    with conn:
        conn.execute("DELETE FROM targets WHERE session_date = ? AND market = ?", (session_date, market))
        conn.executemany("INSERT INTO targets VALUES (?, ?, ?, ?, ?)",
                         [(session_date, market, t, w, p) for t, (w, p) in targets.items()])


def load_targets(conn: sqlite3.Connection, session_date: str, market: str) -> dict[str, tuple[float, float]]:
    return {r["ticker"]: (r["weight"], r["ref_price"]) for r in conn.execute(
        "SELECT ticker, weight, ref_price FROM targets WHERE session_date = ? AND market = ?",
        (session_date, market))}


def save_positions(conn: sqlite3.Connection, date: str, market: str, qty: dict[str, float],
                   prices: dict[str, float]) -> None:
    with conn:
        conn.execute("DELETE FROM positions WHERE date = ? AND market = ?", (date, market))
        conn.executemany("INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?)",
                         [(date, market, t, q, prices.get(t, 1.0), q * prices.get(t, 1.0))
                          for t, q in qty.items() if q != 0 or t == "CASH"])


def latest_positions(conn: sqlite3.Connection, market: str) -> tuple[str | None, dict[str, float]]:
    row = conn.execute("SELECT MAX(date) AS d FROM positions WHERE market = ?", (market,)).fetchone()
    if not row["d"]:
        return None, {}
    return row["d"], {r["ticker"]: r["qty"] for r in conn.execute(
        "SELECT ticker, qty FROM positions WHERE market = ? AND date = ?", (market, row["d"]))}


def save_order(conn: sqlite3.Connection, order: dict) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO orders (session_date, market, ticker, side, step, qty, ref_price, limit_price, "
            "filled_qty, fill_price, cost, status, broker_order_no, created_at, error) VALUES (:session_date, "
            ":market, :ticker, :side, :step, :qty, :ref_price, :limit_price, :filled_qty, :fill_price, :cost, :status, "
            ":broker_order_no, :created_at, :error)",
            {"step": 0, "filled_qty": 0, "fill_price": None, "cost": None, "broker_order_no": None, "error": None,
             "created_at": now_iso(), **order})


def save_equity(conn: sqlite3.Connection, date: str, kr_value: float, us_value: float, usdkrw: float) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO equity VALUES (?, ?, ?, ?, ?)",
                     (date, kr_value, us_value, usdkrw, kr_value + us_value * usdkrw))
