import pandas as pd

from markets import MARKETS, current_session, session_times

KST = "Asia/Seoul"


def kst(ts):
    return ts.tz_convert(KST).strftime("%m-%d %H:%M")


def test_kr_regular_day():
    s = session_times(MARKETS["KR"], "2026-10-27")
    assert (kst(s.signal), kst(s.execute)) == ("10-27 11:00", "10-27 12:00")


def test_kr_late_open_keeps_noon():
    s = session_times(MARKETS["KR"], "2027-01-04")  # opens 10:00
    assert (kst(s.signal), kst(s.execute)) == ("01-04 11:00", "01-04 12:00")


def test_us_midpoint_follows_dst():
    summer = session_times(MARKETS["US"], "2026-10-27")
    winter = session_times(MARKETS["US"], "2026-11-02")
    assert summer.execute.strftime("%H:%M") == winter.execute.strftime("%H:%M") == "12:45"
    assert (kst(summer.signal), kst(summer.execute)) == ("10-28 00:45", "10-28 01:45")
    assert (kst(winter.signal), kst(winter.execute)) == ("11-03 01:45", "11-03 02:45")


def test_us_half_day_midpoint():
    s = session_times(MARKETS["US"], "2026-11-27")  # closes 13:00
    assert (s.signal.strftime("%H:%M"), s.execute.strftime("%H:%M")) == ("10:15", "11:15")


def test_holidays():
    assert session_times(MARKETS["KR"], "2026-12-31") is None
    assert session_times(MARKETS["US"], "2026-11-26") is None  # Thanksgiving


def test_current_session_uses_exchange_local_date():
    # 01:00 KST on 10-28 is still 10-27 in New York
    now = pd.Timestamp("2026-10-28 01:00", tz=KST)
    assert current_session(MARKETS["US"], now).session == "2026-10-27"
    assert current_session(MARKETS["KR"], now).session == "2026-10-28"
