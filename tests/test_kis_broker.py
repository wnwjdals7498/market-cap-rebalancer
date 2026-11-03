import json

import pandas as pd
import pytest

import kis_api
from kis_api import KisClient
from kis_auth import KisCredentials
from kis_broker import KisBroker
from strategy import CASH

OK = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "ok"}


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self._body


class Router:
    """Returns canned bodies per URL suffix; records (method, suffix, tr_id, data)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def _answer(self, method, url, data, headers):
        suffix = url.split("/v1/")[1]
        self.calls.append((method, suffix, headers["tr_id"], data))
        r = self.routes[suffix]
        return Resp(r(data) if callable(r) else r)

    def get(self, url, params, headers, timeout):
        return self._answer("GET", url, params, headers)

    def post(self, url, json, headers, timeout):
        return self._answer("POST", url, json, headers)


@pytest.fixture(autouse=True)
def no_token(monkeypatch):
    monkeypatch.setattr(kis_api, "get_token", lambda creds: ("tok", 9e18))


def broker(routes, env="paper"):
    s = Router(routes)
    c = KisClient(KisCredentials(env, "k", "s", "50123456", "01"), session=s, sleep=lambda x: None,
                  clock=lambda: 0.0, min_interval=0)
    b = KisBroker(c, exchange_of=lambda t: {"IEF": "NAS", "BRK": "NYS"}.get(t, "NAS"),
                  now=lambda: pd.Timestamp("2026-11-04 03:00", tz="UTC"))
    return b, s


def test_kr_balance_and_buying_power():
    b, s = broker({
        "trading/inquire-balance": OK | {"output1": [{"pdno": "005930", "hldg_qty": "137"},
                                                     {"pdno": "148070", "hldg_qty": "0"}],
                                         "output2": [{"prvs_rcdl_excc_amt": "19985000"}]},
        "trading/inquire-psbl-order": OK | {"output": {"nrcvb_buy_qty": "91"}},
    })
    assert b.balance("KR") == {"005930.KS": 137, CASH: 19_985_000.0}
    assert b.buying_power("KR", "005930.KS", 218500.0) == 91
    assert s.calls[0][2] == "VTTC8434R" and s.calls[1][3]["ORD_UNPR"] == "218500"


def test_us_balance_queries_each_exchange_on_paper():
    def bal(p):
        return OK | {"output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10"}] if p["OVRS_EXCG_CD"] == "NASD" else [],
                     "output2": {}}

    b, s = broker({"trading/inquire-balance": bal,
                   "trading/inquire-psamount": OK | {"output": {"ord_psbl_frcr_amt": "96616.80", "ord_psbl_qty": "7"}}})
    assert b.balance("US") == {"AAPL": 10, CASH: 96616.80}
    assert [c[3]["OVRS_EXCG_CD"] for c in s.calls if c[1] == "trading/inquire-balance"] == ["NASD", "NYSE", "AMEX"]
    real, s2 = broker({"trading/inquire-balance": bal,
                       "trading/inquire-psamount": OK | {"output": {"ord_psbl_frcr_amt": "1"}}}, env="real")
    real.balance("US")
    assert [c[3]["OVRS_EXCG_CD"] for c in s2.calls if c[1] == "trading/inquire-balance"] == ["NASD"]
    assert s2.calls[0][2] == "TTTS3012R"


def test_kr_order_status_and_cancel():
    ccld = {"rows": [{"odno": "0000012345", "tot_ccld_qty": "40", "avg_prvs": "218500", "rmn_qty": "60"}]}
    b, s = broker({
        "trading/order-cash": OK | {"output": {"KRX_FWDG_ORD_ORGNO": "00950", "ODNO": "0000012345", "ORD_TMD": "120001"}},
        "trading/inquire-daily-ccld": lambda p: OK | {"output1": ccld["rows"], "output2": {}},
        "trading/order-rvsecncl": OK | {"output": {}},
    })
    no = b.place("KR", "005930.KS", "SELL", 100, 218500.0)
    assert no == "00950:0000012345"
    method, _, tr, body = s.calls[-1]
    assert (method, tr, body["PDNO"], body["ORD_QTY"], body["ORD_UNPR"], body["SLL_TYPE"], body["EXCG_ID_DVSN_CD"]) == \
        ("POST", "VTTC0011U", "005930", "100", "218500", "01", "KRX")
    st = b.status("KR", "005930.KS", no)
    assert (st.filled_qty, st.avg_price, st.open_qty) == (40, 218500.0, 60)
    assert s.calls[-1][3]["INQR_STRT_DT"] == "20261104"  # KST date
    b.cancel("KR", "005930.KS", no, 60)
    body = s.calls[-1][3]
    assert (body["KRX_FWDG_ORD_ORGNO"], body["ORGN_ODNO"], body["RVSE_CNCL_DVSN_CD"], body["QTY_ALL_ORD_YN"]) == \
        ("00950", "0000012345", "02", "Y")
    ccld["rows"] = []
    assert b.status("KR", "005930.KS", no).open_qty == 100  # not visible yet -> still working


def test_us_order_uses_paper_sell_tr_and_order_exchange_code():
    b, s = broker({
        "trading/order": OK | {"output": {"KRX_FWDG_ORD_ORGNO": "01790", "ODNO": "0030123456", "ORD_TMD": "014501"}},
        "trading/inquire-ccnl": OK | {"output": [
            {"odno": "0030123456", "ft_ccld_qty": "3", "ft_ccld_unpr3": "91.50000", "nccs_qty": "2", "rjct_rson": ""}]},
        "trading/order-rvsecncl": {"rt_cd": "1", "msg_cd": "APBK0918", "msg1": "이미 체결된 주문입니다"},
    })
    no = b.place("US", "IEF", "SELL", 5, 91.5)
    _, _, tr, body = s.calls[-1]
    assert (tr, body["OVRS_EXCG_CD"], body["OVRS_ORD_UNPR"], body["SLL_TYPE"], body["ORD_DVSN"]) == \
        ("VTTT1001U", "NASD", "91.50", "00", "00")
    st = b.status("US", "IEF", no)
    assert (st.filled_qty, st.avg_price, st.open_qty) == (3, 91.5, 2)
    assert s.calls[-1][3]["ORD_STRT_DT"] == "20261103"  # New York date
    b.cancel("US", "IEF", no, 2)  # rejection is logged, not raised
    assert s.calls[-1][3]["ORD_QTY"] == "2"
