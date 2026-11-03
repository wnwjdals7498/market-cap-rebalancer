"""KIS Open API REST client and quote source."""
from __future__ import annotations

import logging
import time

import pandas as pd
import requests

from kis_auth import KisCredentials, auth_headers, get_token, load_credentials
from markets import MARKETS, last_trade_time
from sources import Quote, Source

log = logging.getLogger("mcr")

RATE_LIMIT_CODE = "EGW00201"                  # 초당 거래건수 초과
MIN_INTERVAL = {"paper": 1.05, "real": 0.06}  # KIS: paper 1/s, real 18/s per account
US_EXCHANGES = ("NAS", "NYS", "AMS")


class KisApiError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class KisClient:
    def __init__(self, creds: KisCredentials | None = None, session=None, sleep=time.sleep,
                 clock=time.monotonic, min_interval: float | None = None):
        self.creds = creds or load_credentials()
        self.session = session or requests.Session()
        self.sleep, self.clock = sleep, clock
        self.min_interval = MIN_INTERVAL[self.creds.env] if min_interval is None else min_interval
        self._last = float("-inf")

    def _throttle(self) -> None:
        wait = self._last + self.min_interval - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._last = self.clock()

    def get(self, path: str, tr_id: str, params: dict[str, str], retries: int = 3) -> dict:
        return self._request("GET", path, tr_id, params, retries)

    def post(self, path: str, tr_id: str, body: dict[str, str], retries: int = 3) -> dict:
        """Rate-limit rejections (EGW00201) are retried; KIS rejects those before accepting an order."""
        return self._request("POST", path, tr_id, body, retries)

    def _request(self, method: str, path: str, tr_id: str, data: dict[str, str], retries: int) -> dict:
        for attempt in range(retries):
            self._throttle()
            token, _ = get_token(self.creds)
            headers = auth_headers(self.creds, token, tr_id)
            url = self.creds.base_url + path
            if method == "GET":
                r = self.session.get(url, params=data, timeout=10, headers=headers)
            else:
                r = self.session.post(url, json=data, timeout=10, headers=headers)
            try:
                body = r.json()
            except ValueError:
                raise KisApiError(f"HTTP{r.status_code}", r.text[:200]) from None
            if body.get("msg_cd") == RATE_LIMIT_CODE:
                self.sleep(1.0 * (attempt + 1))
                continue
            if r.status_code != 200 or body.get("rt_cd") != "0":
                raise KisApiError(body.get("msg_cd") or f"HTTP{r.status_code}", body.get("msg1", ""))
            return body
        raise KisApiError(RATE_LIMIT_CODE, f"rate limited after {retries} attempts")


def market_of(ticker: str) -> str:
    return "KR" if ticker.endswith((".KS", ".KQ")) else "US"


class KisSource:
    """Quotes for both markets from KIS; KR share counts from KIS.

    US share counts come from `us_shares` because KIS reports only the listed class
    (GOOGL -> Class A, about half of Alphabet's market cap).
    KIS quote responses carry no timestamp: a quote is stamped `now` while its market
    is in session, else the last session close, so freshness checks still reject it.
    """
    name = "kis"

    def __init__(self, client: KisClient, us_shares: Source, now=lambda: pd.Timestamp.now(tz="UTC")):
        self.client, self.us_shares, self.now = client, us_shares, now
        self._exchange: dict[str, str] = {}

    def _kr(self, ticker: str) -> dict:
        return self.client.get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                               {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker.split(".")[0]})["output"]

    def _us(self, ticker: str) -> dict:
        order = [self._exchange[ticker]] if ticker in self._exchange else list(US_EXCHANGES)
        for ex in order:
            out = self.client.get("/uapi/overseas-price/v1/quotations/price", "HHDFS00000300",
                                  {"AUTH": "", "EXCD": ex, "SYMB": ticker})["output"]
            if out.get("last"):  # a wrong exchange answers rt_cd 0 with empty fields
                self._exchange[ticker] = ex
                return out
        raise KisApiError("NOTFOUND", f"{ticker} not listed on {order}")

    def exchange(self, ticker: str) -> str:
        """KIS quote exchange code (NAS/NYS/AMS) of a US ticker."""
        if ticker not in self._exchange:
            self._us(ticker)
        return self._exchange[ticker]

    def quotes(self, tickers: list[str]) -> dict[str, Quote]:
        out = {}
        for t in tickers:
            try:
                m = market_of(t)
                raw = self._kr(t)["stck_prpr"] if m == "KR" else self._us(t)["last"]
                out[t] = Quote(float(raw), last_trade_time(MARKETS[m], self.now()), self.name)
            except (KisApiError, KeyError, ValueError, requests.RequestException) as e:
                log.warning("kis quote %s failed: %s", t, e)
        return out

    def shares(self, tickers: list[str]) -> dict[str, float]:
        us = [t for t in tickers if market_of(t) == "US"]
        out = self.us_shares.shares(us) if us else {}
        for t in tickers:
            if market_of(t) != "KR":
                continue
            try:
                out[t] = float(self._kr(t)["lstn_stcn"])
            except (KisApiError, KeyError, ValueError, requests.RequestException) as e:
                log.warning("kis shares %s failed: %s", t, e)
        return out
