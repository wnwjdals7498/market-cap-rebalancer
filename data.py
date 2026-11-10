"""Price / market-cap data loading (yfinance) with a local CSV cache."""
from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import pandas as pd
import yfinance as yf

from strategy import Config

warnings.filterwarnings("ignore")

CACHE_DIR = Path(__file__).parent / "cache"

# 시총 1위 후보군. 5년 내 1위 근처에 있었던 종목 위주.
US_UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA"]
KR_UNIVERSE = ["005930.KS", "000660.KS", "373220.KS", "207940.KS", "005380.KS"]

FX_TICKER = "KRW=X"  # USD/KRW


def _naive(idx: pd.Index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def download_prices(tickers: list[str], start: str, end: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (close, adj_close). close = split-adjusted only (for market cap),
    adj_close = split + dividend adjusted (for total return)."""
    if end is not None:  # yfinance's end is exclusive; make ours inclusive
        end = str((pd.Timestamp(end) + pd.Timedelta(days=1)).date())
    raw = yf.download(tickers, start=start, end=end, auto_adjust=False, progress=False)
    close, adj = raw["Close"].copy(), raw["Adj Close"].copy()
    close.index = adj.index = _naive(close.index)
    return close, adj


def split_adjusted_shares(ticker: str, start: str, index: pd.DatetimeIndex) -> pd.Series:
    """Shares outstanding history, restated to today's split basis, aligned to `index`.

    yfinance reports share counts as-of each filing (not split adjusted), while
    prices are split adjusted, so we multiply by every split that happened later.
    """
    tk = yf.Ticker(ticker)
    shares = tk.get_shares_full(start=start)
    if shares is None or shares.empty:
        so = tk.info.get("sharesOutstanding")
        return pd.Series(float(so) if so else float("nan"), index=index)

    shares = shares.astype(float)
    shares.index = _naive(shares.index)
    shares = shares.groupby(level=0).last()

    splits = tk.splits
    if not splits.empty:
        splits.index = _naive(splits.index)
        factor = pd.Series(1.0, index=shares.index)
        for d, ratio in splits.items():
            factor[shares.index < d] *= ratio
        shares = shares * factor

    # get_shares_full has occasional junk prints; a short rolling median removes them.
    shares = shares.rolling(5, center=True, min_periods=1).median()
    return shares.reindex(shares.index.union(index)).ffill().bfill().reindex(index)


def load_market_data(start: str, end: str | None = None, extra: list[str] | None = None,
                     refresh: bool = False, mcap_tickers: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Loads close/adj_close for market-cap tickers + extra tickers, FX, and market caps.

    Market cap (USD for US, KR in KRW) = split-adjusted close x split-adjusted shares, computed only for
    `mcap_tickers` (default: the Config default candidates of both markets). It is NaN before a
    ticker's listing date, so callers must leave such days out of the ranking.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    if mcap_tickers is None:
        default = Config()
        mcap_tickers = list(default.us_universe + default.kr_universe)
    mcap_tickers = list(dict.fromkeys(mcap_tickers))
    tickers = sorted(set(mcap_tickers + (extra or []) + [FX_TICKER]))
    digest = hashlib.md5((','.join(tickers) + '|' + ','.join(sorted(mcap_tickers))).encode()).hexdigest()[:8]
    key = f"{start}_{end or 'latest'}_{digest}"
    paths = {n: CACHE_DIR / f"{n}_{key}.csv" for n in ("close", "adj", "mcap")}

    if not refresh and all(p.exists() for p in paths.values()):
        return {n: pd.read_csv(p, index_col=0, parse_dates=True) for n, p in paths.items()}

    close, adj = download_prices(tickers, start, end)
    mcap = pd.DataFrame(index=close.index)
    for t in mcap_tickers:
        mcap[t] = close[t] * split_adjusted_shares(t, start, close.index)

    out = {"close": close, "adj": adj, "mcap": mcap}
    for n, df in out.items():
        df.to_csv(paths[n])
    return out
