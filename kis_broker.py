"""KIS Open API broker: balances, buying power, limit orders, fills and cancels (KR cash + US stocks)."""
from __future__ import annotations

import logging
from typing import Callable

import pandas as pd

from brokers import OrderState
from kis_api import KisApiError, KisClient
from strategy import CASH

log = logging.getLogger("mcr")

# (real, paper) TR IDs from the KIS API docs. Note the paper US sell id is not "V" + real id.
TR = {
    "kr_buy": ("TTTC0012U", "VTTC0012U"), "kr_sell": ("TTTC0011U", "VTTC0011U"),
    "kr_cancel": ("TTTC0013U", "VTTC0013U"), "kr_balance": ("TTTC8434R", "VTTC8434R"),
    "kr_psbl": ("TTTC8908R", "VTTC8908R"), "kr_ccld": ("TTTC0081R", "VTTC0081R"),
    "us_buy": ("TTTT1002U", "VTTT1002U"), "us_sell": ("TTTT1006U", "VTTT1001U"),
    "us_cancel": ("TTTT1004U", "VTTT1004U"), "us_balance": ("TTTS3012R", "VTTS3012R"),
    "us_psamt": ("TTTS3007R", "VTTS3007R"), "us_ccnl": ("TTTS3035R", "VTTS3035R"),
}
ORDER_EXCHANGE = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}  # quote code -> order code
KR_SUFFIX = ".KS"


class KisBroker:
    name = "kis"

    def __init__(self, client: KisClient, exchange_of: Callable[[str], str],
                 now=lambda: pd.Timestamp.now(tz="UTC")):
        self.client, self.exchange_of, self.now = client, exchange_of, now
        self.paper = client.creds.env == "paper"
        self._placed: dict[str, int] = {}

    # --- helpers ---------------------------------------------------------------
    def _tr(self, key: str) -> str:
        real, paper = TR[key]
        return paper if self.paper else real

    def _acct(self) -> dict[str, str]:
        return {"CANO": self.client.creds.cano, "ACNT_PRDT_CD": self.client.creds.acnt_prdt_cd}

    def _today(self, tz: str) -> str:
        return self.now().tz_convert(tz).strftime("%Y%m%d")

    def _us_excg(self, ticker: str) -> str:
        return ORDER_EXCHANGE[self.exchange_of(ticker)]

    # --- Broker ------------------------------------------------------------------
    def balance(self, market: str) -> dict[str, float]:
        if market == "KR":
            body = self.client.get("/uapi/domestic-stock/v1/trading/inquire-balance", self._tr("kr_balance"),
                                   self._acct() | {"AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
                                                   "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                                                   "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                                                   "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""})
            out = {r["pdno"] + KR_SUFFIX: int(r["hldg_qty"]) for r in body["output1"] if int(r["hldg_qty"])}
            out[CASH] = float(body["output2"][0]["prvs_rcdl_excc_amt"])  # D+2 settled cash
            return out
        out: dict[str, float] = {}
        # paper accounts are queried per exchange; real "NASD" already means all US exchanges
        for ex in ("NASD",) if not self.paper else ("NASD", "NYSE", "AMEX"):
            body = self.client.get("/uapi/overseas-stock/v1/trading/inquire-balance", self._tr("us_balance"),
                                   self._acct() | {"OVRS_EXCG_CD": ex, "TR_CRCY_CD": "USD",
                                                   "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""})
            for r in body["output1"]:
                if int(float(r["ovrs_cblc_qty"])):
                    out[r["ovrs_pdno"]] = int(float(r["ovrs_cblc_qty"]))
        out[CASH] = float(self._psamount("NASD", "AAPL", 1.0)["ord_psbl_frcr_amt"])  # USD only, no FX
        return out

    def _psamount(self, excg: str, ticker: str, price: float) -> dict:
        return self.client.get("/uapi/overseas-stock/v1/trading/inquire-psamount", self._tr("us_psamt"),
                               self._acct() | {"OVRS_EXCG_CD": excg, "OVRS_ORD_UNPR": f"{price:.2f}",
                                               "ITEM_CD": ticker})["output"]

    def buying_power(self, market: str, ticker: str, price: float) -> int:
        if market == "KR":
            out = self.client.get("/uapi/domestic-stock/v1/trading/inquire-psbl-order", self._tr("kr_psbl"),
                                  self._acct() | {"PDNO": ticker.split(".")[0], "ORD_UNPR": str(int(price)),
                                                  "ORD_DVSN": "00", "CMA_EVLU_AMT_ICLD_YN": "N",
                                                  "OVRS_ICLD_YN": "N"})["output"]
            return int(out["nrcvb_buy_qty"])  # without margin (미수 없는 매수수량)
        return int(self._psamount(self._us_excg(ticker), ticker, price)["ord_psbl_qty"])

    def place(self, market: str, ticker: str, side: str, qty: int, price: float) -> str:
        if market == "KR":
            body = self.client.post("/uapi/domestic-stock/v1/trading/order-cash",
                                    self._tr("kr_buy" if side == "BUY" else "kr_sell"),
                                    self._acct() | {"PDNO": ticker.split(".")[0], "ORD_DVSN": "00",
                                                    "ORD_QTY": str(qty), "ORD_UNPR": str(int(price)),
                                                    "EXCG_ID_DVSN_CD": "KRX",
                                                    "SLL_TYPE": "01" if side == "SELL" else "", "CNDT_PRIC": ""})
            out = {k.upper(): v for k, v in body["output"].items()}
            order_no = f"{out['KRX_FWDG_ORD_ORGNO']}:{out['ODNO']}"
        else:
            body = self.client.post("/uapi/overseas-stock/v1/trading/order",
                                    self._tr("us_buy" if side == "BUY" else "us_sell"),
                                    self._acct() | {"OVRS_EXCG_CD": self._us_excg(ticker), "PDNO": ticker,
                                                    "ORD_QTY": str(qty), "OVRS_ORD_UNPR": f"{price:.2f}",
                                                    "CTAC_TLNO": "", "MGCO_APTM_ODNO": "",
                                                    "SLL_TYPE": "00" if side == "SELL" else "",
                                                    "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00"})
            order_no = {k.upper(): v for k, v in body["output"].items()}["ODNO"]
        self._placed[order_no] = qty
        log.info("%s order %s %s %d @ %s -> %s", market, side, ticker, qty, price, order_no)
        return order_no

    def status(self, market: str, ticker: str, order_no: str) -> OrderState:
        placed = self._placed.get(order_no, 0)
        if market == "KR":
            odno = order_no.split(":")[1]
            day = self._today("Asia/Seoul")
            rows = self.client.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld", self._tr("kr_ccld"),
                                   self._acct() | {"INQR_STRT_DT": day, "INQR_END_DT": day, "SLL_BUY_DVSN_CD": "00",
                                                   "PDNO": "", "CCLD_DVSN": "00", "INQR_DVSN": "00",
                                                   "INQR_DVSN_3": "00", "ORD_GNO_BRNO": "", "ODNO": odno,
                                                   "INQR_DVSN_1": "", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
                                                   "EXCG_ID_DVSN_CD": "KRX"})["output1"]
            row = next((r for r in rows if int(r["odno"]) == int(odno)), None)
            if row is None:  # not visible yet
                return OrderState(order_no, 0, None, placed)
            filled = int(row["tot_ccld_qty"])
            return OrderState(order_no, filled, float(row["avg_prvs"]) if filled else None, int(row["rmn_qty"]))

        day = self._today("America/New_York")
        # paper accounts only accept "" (all) for these filters
        rows = self.client.get("/uapi/overseas-stock/v1/trading/inquire-ccnl", self._tr("us_ccnl"),
                               self._acct() | {"PDNO": "" if self.paper else ticker, "ORD_STRT_DT": day,
                                               "ORD_END_DT": day, "SLL_BUY_DVSN": "00", "CCLD_NCCS_DVSN": "00",
                                               "OVRS_EXCG_CD": "" if self.paper else self._us_excg(ticker),
                                               "SORT_SQN": "DS", "ORD_DT": "", "ORD_GNO_BRNO": "", "ODNO": "",
                                               "CTX_AREA_NK200": "", "CTX_AREA_FK200": ""})["output"]
        row = next((r for r in rows if int(r["odno"]) == int(order_no)), None)
        if row is None:
            return OrderState(order_no, 0, None, placed)
        filled = int(float(row["ft_ccld_qty"]))
        open_qty = 0 if row.get("rjct_rson") else int(float(row["nccs_qty"]))
        return OrderState(order_no, filled, float(row["ft_ccld_unpr3"]) if filled else None, open_qty)

    def cancel(self, market: str, ticker: str, order_no: str, open_qty: int) -> None:
        try:
            if market == "KR":
                orgno, odno = order_no.split(":")
                self.client.post("/uapi/domestic-stock/v1/trading/order-rvsecncl", self._tr("kr_cancel"),
                                 self._acct() | {"KRX_FWDG_ORD_ORGNO": orgno, "ORGN_ODNO": odno, "ORD_DVSN": "00",
                                                 "RVSE_CNCL_DVSN_CD": "02", "ORD_QTY": "0", "ORD_UNPR": "0",
                                                 "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX"})
            else:
                self.client.post("/uapi/overseas-stock/v1/trading/order-rvsecncl", self._tr("us_cancel"),
                                 self._acct() | {"OVRS_EXCG_CD": self._us_excg(ticker), "PDNO": ticker,
                                                 "ORGN_ODNO": order_no, "RVSE_CNCL_DVSN_CD": "02",
                                                 "ORD_QTY": str(open_qty), "OVRS_ORD_UNPR": "0",
                                                 "MGCO_APTM_ODNO": "", "ORD_SVR_DVSN_CD": "0"})
        except KisApiError as e:  # e.g. filled in the meantime; the caller re-reads the status
            log.warning("%s cancel %s %s failed: %s", market, ticker, order_no, e)
