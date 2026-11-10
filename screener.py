"""Weekly candidate-pool search: the largest common stocks of a market by market cap (yfinance screener)."""
from __future__ import annotations

import re
from typing import Callable

REGIONS = {"US": "us", "KR": "kr"}
KR_COMMON = r"^\d{5}0\.KS$"  # KOSPI common stock: preferred shares end in a non-zero digit
MAX_COUNT = 250  # yfinance screener page limit


class ScreenError(RuntimeError):
    pass


def yf_fetch(region: str, count: int) -> list[dict]:
    import yfinance as yf

    query = yf.EquityQuery("eq", ["region", region])
    result = yf.screen(query, sortField="intradaymarketcap", sortAsc=False, size=count)
    return result["quotes"]


def screen_top(market: str, size: int, fetch: Callable[[str, int], list[dict]] | None = None) -> list[str]:
    """The `size` largest common stocks of `market` ("US" or "KR"), largest market cap first.

    Only EQUITY quotes count; KR keeps KOSPI common stock only. Several listings of one company
    (same `longName`, e.g. GOOGL/GOOG) collapse to the one with the lowest price, since trades are in
    whole shares. Raises ScreenError if fewer than `size` stocks remain."""
    if market not in REGIONS:
        raise ScreenError(f"Unknown market: {market}")
    fetch = fetch or yf_fetch
    try:
        quotes = fetch(REGIONS[market], min(size * 3, MAX_COUNT))
    except ScreenError:
        raise
    except Exception as exc:
        raise ScreenError(f"{market} screen failed: {exc}") from exc

    def usable(q: dict) -> bool:
        symbol = q.get("symbol")
        if not isinstance(symbol, str) or q.get("quoteType") != "EQUITY":
            return False
        if market == "KR" and not re.fullmatch(KR_COMMON, symbol):
            return False
        return all(isinstance(q.get(k), (int, float)) and q[k] > 0 for k in ("marketCap", "regularMarketPrice"))

    best: dict[str, dict] = {}
    for q in filter(usable, quotes):
        key = q.get("longName") or q["symbol"]
        if key not in best or q["regularMarketPrice"] < best[key]["regularMarketPrice"]:
            best[key] = q
    ranked = sorted(best.values(), key=lambda q: q["marketCap"], reverse=True)
    if len(ranked) < size:
        raise ScreenError(f"{market} screen returned {len(ranked)} usable stocks, need {size}")
    return [q["symbol"] for q in ranked[:size]]
