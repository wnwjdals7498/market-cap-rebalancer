"""Live data sources. Phase 1: yfinance only; KRX / SEC / KIS can implement `Source` later."""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Protocol

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")


@dataclass(frozen=True)
class Quote:
    price: float
    time: pd.Timestamp  # tz-aware time of the last trade/bar
    source: str = ""


class Source(Protocol):
    name: str

    def shares(self, tickers: list[str]) -> dict[str, float]: ...
    def quotes(self, tickers: list[str]) -> dict[str, Quote]: ...


class YFinanceSource:
    """Unofficial Yahoo data. KRX quotes are delayed (~20 min), which is fine for ranking."""
    name = "yfinance"

    def shares(self, tickers: list[str]) -> dict[str, float]:
        out = {}
        for t in tickers:
            info = yf.Ticker(t).info
            # implied = market cap / price, i.e. all share classes (GOOGL A+B+C); plain
            # sharesOutstanding only counts the listed class.
            s = info.get("impliedSharesOutstanding") or info.get("sharesOutstanding")
            if s:
                out[t] = float(s)
        return out

    def quotes(self, tickers: list[str]) -> dict[str, Quote]:
        bars = yf.download(tickers, period="1d", interval="1m", auto_adjust=False, progress=False,
                           group_by="column")["Close"]
        if isinstance(bars, pd.Series):
            bars = bars.to_frame(tickers[0])
        out = {}
        for t in tickers:
            if t not in bars:
                continue
            s = bars[t].dropna()
            if not s.empty:
                out[t] = Quote(float(s.iloc[-1]), s.index[-1], self.name)
        return out  # tickers without a bar are omitted; callers treat them as missing


class FallbackSource:
    """Per-ticker fallback: whatever `primary` cannot answer is asked of `secondary`."""

    def __init__(self, primary: Source, secondary: Source):
        self.primary, self.secondary = primary, secondary
        self.name = f"{primary.name}+{secondary.name}"

    def shares(self, tickers: list[str]) -> dict[str, float]:
        out = self.primary.shares(tickers)
        missing = [t for t in tickers if t not in out]
        return out | (self.secondary.shares(missing) if missing else {})

    def quotes(self, tickers: list[str]) -> dict[str, Quote]:
        out = self.primary.quotes(tickers)
        missing = [t for t in tickers if t not in out]
        return out | (self.secondary.quotes(missing) if missing else {})
