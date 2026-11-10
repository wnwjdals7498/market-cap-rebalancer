import pytest

import screener


def quote(symbol, cap, price=100.0, name=None, kind="EQUITY"):
    return {"symbol": symbol, "quoteType": kind, "marketCap": cap, "regularMarketPrice": price,
            "longName": name or symbol}


def fake(quotes, calls=None):
    def fetch(region, count):
        if calls is not None:
            calls.append((region, count))
        return quotes
    return fetch


def test_screen_top_filters_and_dedupes():
    us = [quote("MSFT", 3000), quote("GOOGL", 2000, 180.0, "Alphabet Inc."), quote("GOOG", 2000, 175.0, "Alphabet Inc."),
          quote("SPY", 9999, kind="ETF"), quote("AAPL", 3500), quote("NVDA", 4000), quote("AMZN", 1900)]
    calls = []
    assert screener.screen_top("US", 4, fake(us, calls)) == ["NVDA", "AAPL", "MSFT", "GOOG"]  # sorted, cut, one Alphabet
    assert calls == [("us", 12)]  # size * 3
    assert screener.screen_top("US", 2, fake(us, calls)) == ["NVDA", "AAPL"]
    assert calls[-1] == ("us", 6)

    kr = [quote("005930.KS", 500), quote("005935.KS", 1052), quote("247540.KQ", 800), quote("000660.KS", 300),
          quote("069500.KS", 900, kind="ETF"), quote("373220.KS", 200)]
    assert screener.screen_top("KR", 3, fake(kr)) == ["005930.KS", "000660.KS", "373220.KS"]
    with pytest.raises(screener.ScreenError):
        screener.screen_top("KR", 4, fake(kr))  # only three usable stocks

    assert screener.screen_top("US", 1, lambda r, c: [quote("X", 1)] * 2) == ["X"]  # same company twice
    assert screener.screen_top("US", 1, fake([quote("A", 0), quote("B", 5)])) == ["B"]  # no market cap


def test_screen_count_is_capped_and_fetch_errors_become_screen_error():
    calls = []
    with pytest.raises(screener.ScreenError):
        screener.screen_top("US", 100, fake([], calls))
    assert calls == [("us", 250)]

    def boom(region, count):
        raise ConnectionError("down")

    with pytest.raises(screener.ScreenError, match="down"):
        screener.screen_top("US", 5, boom)
    with pytest.raises(screener.ScreenError):
        screener.screen_top("JP", 5, fake([]))
