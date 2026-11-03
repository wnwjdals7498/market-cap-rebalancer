import json

import pandas as pd
import pytest

import db
import kis_api
from kis_api import KisApiError, KisClient, KisSource
from kis_auth import KisCredentials
from markets import MARKETS, last_trade_time
from sources import FallbackSource, Quote

CREDS = KisCredentials("paper", "key", "secret", "50123456", "01")
OK = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리 되었습니다."}


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    """Answers by (path suffix, params); records calls."""

    def __init__(self, kr=None, us=None, fail=()):
        self.kr, self.us, self.fail, self.calls = kr or {}, us or {}, set(fail), []

    def get(self, url, params, headers, timeout):
        self.calls.append((url.rsplit("/", 1)[-1], dict(params), headers["tr_id"]))
        if url.endswith("inquire-price"):
            code = params["FID_INPUT_ISCD"]
            if code in self.fail:
                return Resp({"rt_cd": "1", "msg_cd": "EGW00999", "msg1": "boom"}, 500)
            return Resp(OK | {"output": self.kr[code]})
        key = (params["SYMB"], params["EXCD"])
        return Resp(OK | {"output": self.us.get(key, {"last": "", "base": ""})})


@pytest.fixture(autouse=True)
def no_token(monkeypatch):
    monkeypatch.setattr(kis_api, "get_token", lambda creds: ("tok", 9e18))


def client(session, sleeps=None):
    t = {"now": 0.0}

    def sleep(s):
        (sleeps if sleeps is not None else []).append(s)
        t["now"] += s

    return KisClient(CREDS, session=session, sleep=sleep, clock=lambda: t["now"])


def test_client_throttles_to_paper_rate():
    sleeps = []
    c = client(FakeSession(kr={"005930": {"stck_prpr": "1"}}), sleeps)
    for _ in range(3):
        c.get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100", {"FID_INPUT_ISCD": "005930"})
    assert sleeps == [pytest.approx(1.05), pytest.approx(1.05)]


def test_client_retries_rate_limit_then_raises_errors():
    class Flaky(FakeSession):
        n = 0

        def get(self, url, params, headers, timeout):
            Flaky.n += 1
            if Flaky.n == 1:
                return Resp({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}, 500)
            return super().get(url, params, headers, timeout)

    c = client(Flaky(kr={"005930": {"stck_prpr": "7"}}))
    assert c.get("/x/inquire-price", "T", {"FID_INPUT_ISCD": "005930"})["output"]["stck_prpr"] == "7"
    with pytest.raises(KisApiError, match="EGW00999"):
        client(FakeSession(fail={"005930"})).get("/x/inquire-price", "T", {"FID_INPUT_ISCD": "005930"})


def source(session, now):
    class YfShares:
        name = "yf"

        def shares(self, tickers):
            return {t: 1e9 for t in tickers}

    return KisSource(client(session), us_shares=YfShares(), now=lambda: pd.Timestamp(now))


def test_quotes_find_us_exchange_once_and_stamp_time():
    s = FakeSession(kr={"005930": {"stck_prpr": "219250", "lstn_stcn": "5846278608"}},
                    us={("IEF", "NYS"): {"last": "91.5"}, ("AAPL", "NAS"): {"last": "338.32"}})
    src = source(s, "2026-11-03 02:00+00:00")  # 11:00 KST, US closed (21:00 ET prev day)
    q = src.quotes(["005930.KS", "AAPL", "IEF"])
    assert {t: v.price for t, v in q.items()} == {"005930.KS": 219250.0, "AAPL": 338.32, "IEF": 91.5}
    assert q["005930.KS"].time == pd.Timestamp("2026-11-03 02:00+00:00") and q["005930.KS"].source == "kis"
    assert q["AAPL"].time == pd.Timestamp("2026-11-02 16:00", tz="America/New_York")  # last US close
    src.quotes(["IEF"])
    ief_calls = [c[1]["EXCD"] for c in s.calls if c[1].get("SYMB") == "IEF"]
    assert ief_calls == ["NAS", "NYS", "NYS"]  # exchange remembered after the first hit


def test_failed_tickers_are_omitted_and_shares_split_by_market():
    s = FakeSession(kr={"005930": {"stck_prpr": "1", "lstn_stcn": "5846278608"}}, fail={"000660"})
    src = source(s, "2026-11-03 02:00+00:00")
    assert set(src.quotes(["005930.KS", "000660.KS"])) == {"005930.KS"}
    assert src.shares(["005930.KS", "000660.KS", "GOOGL"]) == {"GOOGL": 1e9, "005930.KS": 5846278608.0}


def test_fallback_source_fills_missing_tickers():
    class Fixed:
        def __init__(self, name, prices):
            self.name, self.prices = name, prices

        def quotes(self, tickers):
            return {t: Quote(self.prices[t], pd.Timestamp("2026-11-03", tz="UTC"), self.name)
                    for t in tickers if t in self.prices}

        def shares(self, tickers):
            return {t: 1.0 for t in tickers if t in self.prices}

    f = FallbackSource(Fixed("kis", {"A": 1.0}), Fixed("yfinance", {"A": 9.0, "B": 2.0}))
    q = f.quotes(["A", "B", "C"])
    assert {t: (v.price, v.source) for t, v in q.items()} == {"A": (1.0, "kis"), "B": (2.0, "yfinance")}
    assert f.name == "kis+yfinance"


def test_last_trade_time():
    kr = MARKETS["KR"]
    assert last_trade_time(kr, pd.Timestamp("2026-11-03 11:00", tz="Asia/Seoul")) == \
        pd.Timestamp("2026-11-03 11:00", tz="Asia/Seoul")
    assert last_trade_time(kr, pd.Timestamp("2026-11-03 08:00", tz="Asia/Seoul")) == \
        pd.Timestamp("2026-11-02 15:30", tz="Asia/Seoul")   # before open -> previous close
    assert last_trade_time(kr, pd.Timestamp("2026-11-03 17:00", tz="Asia/Seoul")) == \
        pd.Timestamp("2026-11-03 15:30", tz="Asia/Seoul")
    assert last_trade_time(kr, pd.Timestamp("2026-11-08 12:00", tz="Asia/Seoul")) == \
        pd.Timestamp("2026-11-06 15:30", tz="Asia/Seoul")   # Sunday -> Friday close


def test_latest_shares_can_filter_by_source(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.save_shares(conn, "2026-11-02", {"005380.KS": 261.8e6}, "yfinance")
    db.save_shares(conn, "2026-11-03", {"005380.KS": 203.5e6}, "kis+yfinance")
    assert db.latest_shares(conn, ["005380.KS"], source="yfinance") == {"005380.KS": ("2026-11-02", 261.8e6)}
    assert db.latest_shares(conn, ["005380.KS"])["005380.KS"][1] == 203.5e6
