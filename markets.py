"""Market definitions and per-session job times (exchange calendars handle holidays, DST, half days)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time, timedelta
from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd

SIGNAL_LEAD = timedelta(hours=1)  # 시총 조회/목표 계산은 거래 1시간 전


@dataclass(frozen=True)
class Market:
    code: str               # "US" | "KR"
    calendar: str           # exchange_calendars code
    currency: str
    execute_at: time | None  # local exchange time; None = session midpoint


MARKETS = {
    "KR": Market("KR", "XKRX", "KRW", time(12, 0)),
    "US": Market("US", "XNYS", "USD", None),
}


@dataclass(frozen=True)
class SessionTimes:
    session: str            # YYYY-MM-DD (exchange local date)
    open: pd.Timestamp
    close: pd.Timestamp
    signal: pd.Timestamp
    execute: pd.Timestamp


@lru_cache
def _calendar(code: str):
    return xcals.get_calendar(code)


def session_times(market: Market, day: str | pd.Timestamp) -> SessionTimes | None:
    """Job times for `day` (exchange local date), None if the exchange is closed."""
    cal = _calendar(market.calendar)
    day = pd.Timestamp(day).strftime("%Y-%m-%d")
    if not cal.is_session(day):
        return None
    tz = cal.tz
    open_, close = cal.session_open(day).tz_convert(tz), cal.session_close(day).tz_convert(tz)
    midpoint = (open_ + (close - open_) / 2).floor("15min")
    execute = midpoint
    if market.execute_at is not None:
        fixed = pd.Timestamp.combine(pd.Timestamp(day).date(), market.execute_at).tz_localize(tz)
        # keep the fixed time only if it leaves room for the signal job and trading inside the session
        if open_ <= fixed - SIGNAL_LEAD and fixed + timedelta(minutes=30) <= close:
            execute = fixed
    return SessionTimes(day, open_, close, max(execute - SIGNAL_LEAD, open_), execute)


def last_trade_time(market: Market, now: pd.Timestamp) -> pd.Timestamp:
    """`now` while the market is in session, otherwise the most recent session close."""
    cal = _calendar(market.calendar)
    day = now.tz_convert(cal.tz).strftime("%Y-%m-%d")
    s = session_times(market, day)
    if s is not None and s.open <= now < s.close:
        return now
    if s is not None and now >= s.close:
        return s.close
    prev = cal.date_to_session(day, direction="previous")
    if prev == pd.Timestamp(day):  # today is a session but it has not opened yet
        prev = cal.previous_session(prev)
    return cal.session_close(prev).tz_convert(cal.tz)


def current_session(market: Market, now: pd.Timestamp) -> SessionTimes | None:
    """Session whose local date is today at `now` (tz-aware)."""
    local = now.tz_convert(_calendar(market.calendar).tz)
    return session_times(market, local.normalize().tz_localize(None))
