"""Backtest tests on synthetic prices (no network)."""
from datetime import date

import numpy as np
import pandas as pd

import backtest
import data
from strategy import Config

US = ("A", "B", "C", "D", "E")
KR = ("K1.KS", "K2.KS", "K3.KS", "K4.KS", "K5.KS")
CFG = Config(us_universe=US, kr_universe=KR, us_short_bond="SHY", us_long_bond="TLT",
             kr_short_bond="B1.KS", kr_long_bond="B2.KS", confirm_days=0, fx_enabled=False,
             kr_taxable=frozenset(KR))


def make_md(days: int = 10, us_growth: float = 0.0, listed_at: dict[str, int] | None = None,
            first: str = "2024-01-01") -> dict[str, pd.DataFrame]:
    """Prices drift by a per-ticker rate (US ones add `us_growth`); mcap order A>B>C>D>E, KR likewise."""
    idx = pd.bdate_range(first, periods=days)
    names = [*US, *KR, "SHY", "TLT", "B1.KS", "B2.KS", backtest.US_INDEX, backtest.KR_INDEX, data.FX_TICKER]
    steps = np.arange(days)[:, None]
    rates = np.array([0.001 * (i % 5 - 2) + (us_growth if n in (*US, "SHY", "TLT", backtest.US_INDEX) else 0.0)
                      for i, n in enumerate(names)])
    close = pd.DataFrame(100.0 * (1 + rates) ** steps, index=idx, columns=names)
    close[data.FX_TICKER] = 1000.0
    mcap = pd.DataFrame({t: 1e12 * (10 - i) * np.ones(days) for i, t in enumerate((*US, *KR))}, index=idx)
    for ticker, n in (listed_at or {}).items():  # not listed before day n
        close.loc[idx[:n], ticker] = np.nan
        mcap.loc[idx[:n], ticker] = np.nan
    return {"close": close, "adj": close.copy(), "mcap": mcap}


def test_backtest_weekly_trades_once_per_week():
    md = make_md(10)  # Mon 2024-01-01 .. Fri 2024-01-12
    res = backtest.run(md, CFG, "2024-01-01", "2024-01-12", 1e6)
    # day 1 has no previous close to rank on, so the first week trades Tuesday
    expected = [date(2024, 1, 2), date(2024, 1, 8)]
    assert res["trade_days"] == {"US": expected, "KR": expected}

    daily = backtest.run(md, Config(**{**CFG.__dict__, "trade_every": "day"}), "2024-01-01", "2024-01-12", 1e6)
    assert len(daily["trade_days"]["US"]) == 9


def test_backtest_handles_ticker_listed_midway():
    md = make_md(10, listed_at={"E": 5})
    md["mcap"]["E"] = md["mcap"]["E"] * 100  # biggest once it is listed
    cfg = Config(**{**CFG.__dict__, "trade_every": "day"})
    res = backtest.run(md, cfg, "2024-01-01", "2024-01-12", 1e6)
    curve = res["curve"]
    assert curve["total_krw"].notna().all()
    assert curve["US:E"].iloc[:6].fillna(0).eq(0).all()  # signal lags one day behind the listing
    assert curve["US:E"].iloc[-1] > 0
    assert "US:A" in curve and curve["US:A"].iloc[2] > 0  # ranked on the remaining names meanwhile


def test_backtest_fx_moves_cash_toward_us_pct():
    md = make_md(30, us_growth=0.015)  # US assets outgrow KR, pushing the US share above 60%
    base = Config(**{**CFG.__dict__, "trade_every": "day", "fx_band_pp": 2.0})
    off = backtest.run(md, base, "2024-01-01", "2024-02-09", 1e6)
    on = backtest.run(md, Config(**{**base.__dict__, "fx_enabled": True}), "2024-01-01", "2024-02-09", 1e6)

    def us_share(res: dict) -> float:
        last = res["curve"].iloc[-1]
        return last["us_usd"] * last["usdkrw"] / last["total_krw"]

    assert off["fx_moves"] == 0 and on["fx_moves"] > 0
    assert us_share(off) > 0.62
    assert abs(us_share(on) - 0.6) < abs(us_share(off) - 0.6)
    assert on["cost_krw"] > off["cost_krw"]  # exchange spread paid
    assert on["curve"]["kr_krw"].iloc[-1] > off["curve"]["kr_krw"].iloc[-1]
