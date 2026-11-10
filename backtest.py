"""5-year backtest of the rank-weight strategy (top-5 stocks by market cap + short/long bond ETFs).

    python backtest.py                      # default: Config values (weekly trades, 10% per trade)
    python backtest.py --rank-weights 100,0,0,0,0 --trade-every day --confirm-days 3
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import data
from strategy import (CASH, Config, RankState, fx_amount, fx_gap, needs_moves, pocket_value, pocket_weights,
                      rebalance_moves, stock_weights, trade_due, update_ranking)

OUT_DIR = Path(__file__).parent / "results"
US_INDEX, KR_INDEX = "SPY", "069500.KS"  # S&P500, KODEX 200
MARKETS = ("US", "KR")


def _priced(prices: dict[str, float], tickers: set[str]) -> bool:
    return all(np.isfinite(prices.get(t, np.nan)) for t in tickers)


def run(md: dict[str, pd.DataFrame], cfg: Config, start: str, end: str | None,
        capital_krw: float = 100_000_000) -> dict:
    """Simulate `cfg` on `md` (see data.load_market_data).

    Each open day the market's ranking is updated from the previous close's market caps (tickers
    without one, e.g. not yet listed, are left out). Trades happen only on the first day of each
    `trade_every` period. FX: when the US share of total assets leaves the band, the pocket with
    too much reserves cash on its next trade day, the user exchanges it the following day
    (minus `fx_spread`), and the receiving pocket invests it on its next trade day.
    `fx_wait_days` is not used: the user is assumed to exchange right away.
    """
    close, adj, mcap = md["close"], md["adj"].ffill(), md["mcap"]
    fx = adj[data.FX_TICKER]
    open_ = {"US": close[US_INDEX].notna(), "KR": close[KR_INDEX].notna()}
    # signal: ranking as of the previous close of that market (no look-ahead)
    lagged = {m: mcap.reindex(columns=list(cfg.universe(m))).where(open_[m], axis=0).ffill().shift(1)
              for m in MARKETS}

    days = close.loc[start:end].index
    days = days[(open_["US"] | open_["KR"]).loc[days]]
    rate0 = fx.loc[:days[0]].iloc[-1]
    pockets = {"US": {CASH: capital_krw * cfg.us_pct / 100 / rate0},
               "KR": {CASH: capital_krw * (1 - cfg.us_pct / 100)}}
    states = {m: RankState() for m in MARKETS}
    last_trade: dict[str, date | None] = {m: None for m in MARKETS}
    trade_days: dict[str, list[date]] = {m: [] for m in MARKETS}
    rank_changes = {m: 0 for m in MARKETS}
    plan: dict | None = None  # {"from": market, "amount": pocket currency, "reserved": day | None}
    fx_moves = 0
    cost_krw = traded_krw = 0.0
    rows, events = [], []

    for d in days:
        rate = fx.loc[d]
        prices = adj.loc[d].to_dict()
        is_open = {m: bool(open_[m].loc[d]) for m in MARKETS}
        k = {"US": rate, "KR": 1.0}

        if plan is not None and plan["reserved"] is not None and plan["reserved"] < d:
            src, dst = plan["from"], "KR" if plan["from"] == "US" else "US"
            amount = min(plan["amount"], max(pockets[src].get(CASH, 0.0), 0.0))
            fee = amount * k[src] * cfg.fx_spread
            pockets[src][CASH] = pockets[src].get(CASH, 0.0) - amount
            pockets[dst][CASH] = pockets[dst].get(CASH, 0.0) + (amount * k[src] - fee) / k[dst]
            cost_krw += fee
            traded_krw += amount * k[src]
            fx_moves += 1
            plan = None

        if plan is None and all(is_open.values()):
            us_krw = pocket_value(pockets["US"], prices) * rate
            kr_krw = pocket_value(pockets["KR"], prices)
            gap = fx_gap(us_krw, kr_krw, cfg)
            if gap:
                src = "KR" if gap > 0 else "US"
                amount_krw = fx_amount(gap, kr_krw if src == "KR" else us_krw, cfg)
                plan = {"from": src, "amount": amount_krw / k[src], "reserved": None}

        for m, units in pockets.items():
            if not is_open[m]:
                continue
            universe = cfg.universe(m)
            if len(universe) == 1:  # fixed holding (e.g. index benchmark), no ranking needed
                ranked = list(universe)
            else:
                row = lagged[m].loc[d].dropna()
                if row.empty:  # need at least one signal day
                    continue
                ranked = list(row.sort_values(ascending=False, kind="stable").index)
            st = states[m]
            before = list(st.confirmed)
            ranking = update_ranking(st, ranked, cfg)
            if before and ranking != before:
                rank_changes[m] += 1
                events.append({"date": d.date(), "market": m, "ranking": "/".join(ranking)})
            if not trade_due(d.date(), last_trade[m], cfg.trade_every):
                continue

            weights = pocket_weights(stock_weights(ranking, cfg), m, cfg)
            held = {t for t, u in units.items() if t != CASH and u > 1e-12}
            if not _priced(prices, set(weights) | held):  # retry on the next session
                continue
            last_trade[m] = d.date()
            reserving = plan if plan is not None and plan["from"] == m and plan["reserved"] is None else None
            value = pocket_value(units, prices)
            if reserving is not None and value > 0:  # keep `amount` as cash: same as investable = value - amount
                scale = max(value - reserving["amount"], 0.0) / value
                weights = {t: w * scale for t, w in weights.items()}
            if needs_moves(units, prices, weights, cfg):
                new, cost, traded = rebalance_moves(units, prices, weights, m, cfg)
                pockets[m] = new
                cost_krw += cost * k[m]
                traded_krw += traded * k[m]
                trade_days[m].append(d.date())
            if reserving is not None:
                reserving["reserved"] = d

        us_val = pocket_value(pockets["US"], prices)
        kr_val = pocket_value(pockets["KR"], prices)
        hold = {f"{m}:{t}": u * prices.get(t, 1.0) * k[m]
                for m, p in pockets.items() for t, u in p.items() if t != CASH and u > 1e-12}
        rows.append({"date": d, "total_krw": us_val * rate + kr_val, "us_usd": us_val, "kr_krw": kr_val,
                     "usdkrw": rate, **hold})

    curve = pd.DataFrame(rows).set_index("date")
    return {"curve": curve, "cost_krw": cost_krw, "traded_krw": traded_krw, "fx_moves": fx_moves,
            "rank_changes": rank_changes, "trade_days": trade_days, "events": pd.DataFrame(events)}


def metrics(curve: pd.Series) -> dict[str, float]:
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    total = curve.iloc[-1] / curve.iloc[0] - 1
    # weekly returns avoid the mixed US/KR holiday calendar inflating/deflating daily vol
    wk = curve.resample("W-FRI").last().pct_change().dropna()
    vol = wk.std() * np.sqrt(52)
    cagr = (1 + total) ** (1 / years) - 1
    dd = curve / curve.cummax() - 1
    return {"total_return": total, "cagr": cagr, "vol": vol, "sharpe": cagr / vol if vol else np.nan,
            "mdd": dd.min()}


def yearly(curve: pd.Series) -> pd.Series:
    ye = curve.resample("YE").last()
    ye = pd.concat([pd.Series([curve.iloc[0]], index=[curve.index[0]]), ye])
    r = ye.pct_change().dropna()
    r.index = r.index.year
    return r


def _tickers(text: str) -> tuple[str, ...]:
    return tuple(t.strip() for t in text.split(",") if t.strip())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    defaults = Config()
    ap.add_argument("--start", default=None, help="default: 5 years before --end/today")
    ap.add_argument("--end", default=None)
    ap.add_argument("--capital", type=float, default=100_000_000, help="initial capital, KRW")
    for name in ("stock_pct", "us_pct", "band_pp", "step_pct", "short_bond_pct", "fx_band_pp"):
        ap.add_argument("--" + name.replace("_", "-"), type=float, default=getattr(defaults, name))
    for name in ("confirm_days", "fx_wait_days"):
        ap.add_argument("--" + name.replace("_", "-"), type=int, default=getattr(defaults, name))
    ap.add_argument("--rank-weights", default=",".join(f"{w:g}" for w in defaults.rank_weights),
                    help="stock weights for ranks 1..5 (%%), comma separated")
    ap.add_argument("--trade-every", choices=["day", "week", "month"], default=defaults.trade_every)
    ap.add_argument("--no-fx", action="store_true", help="never move cash between the US and KR pockets")
    for name in ("us_universe", "kr_universe"):
        ap.add_argument("--" + name.replace("_", "-"), type=_tickers, default=getattr(defaults, name),
                        help="comma separated tickers")
    for name in ("us_short_bond", "us_long_bond", "kr_short_bond", "kr_long_bond"):
        ap.add_argument("--" + name.replace("_", "-"), default=getattr(defaults, name))
    ap.add_argument("--no-cost", action="store_true", help="zero fees/taxes/fx spread")
    ap.add_argument("--refresh", action="store_true", help="re-download data")
    a = ap.parse_args()

    end_ts = pd.Timestamp(a.end) if a.end else pd.Timestamp.today().normalize()
    start = a.start or str((end_ts - pd.DateOffset(years=5)).date())
    fetch_start = str((pd.Timestamp(start) - pd.DateOffset(days=40)).date())

    cfg = Config(stock_pct=a.stock_pct, us_pct=a.us_pct, band_pp=a.band_pp, step_pct=a.step_pct,
                 confirm_days=a.confirm_days, short_bond_pct=a.short_bond_pct, fx_band_pp=a.fx_band_pp,
                 fx_wait_days=a.fx_wait_days, trade_every=a.trade_every, fx_enabled=not a.no_fx,
                 rank_weights=tuple(float(w) for w in a.rank_weights.split(",")),
                 us_universe=a.us_universe, kr_universe=a.kr_universe,
                 us_short_bond=a.us_short_bond, us_long_bond=a.us_long_bond,
                 kr_short_bond=a.kr_short_bond, kr_long_bond=a.kr_long_bond,
                 kr_taxable=frozenset(a.kr_universe))
    if a.no_cost:
        cfg = replace(cfg, us_fee=0, kr_fee=0, kr_stock_sell_tax=0, fx_spread=0)

    bonds = [*cfg.bonds("US"), *cfg.bonds("KR")]
    md = data.load_market_data(fetch_start, a.end, extra=[*bonds, US_INDEX, KR_INDEX], refresh=a.refresh,
                               mcap_tickers=[*cfg.us_universe, *cfg.kr_universe])

    scenarios = {
        "전략 (설정값)": cfg,
        "1위 단독 (100/0/0/0/0)": replace(cfg, rank_weights=(100, 0, 0, 0, 0)),
        "순위 대기 3일": replace(cfg, confirm_days=3),
        "매매 일간": replace(cfg, trade_every="day"),
        "매매 월간": replace(cfg, trade_every="month"),
        "이동 상한 100% (즉시)": replace(cfg, step_pct=100),
        "같은 60/40, 지수 (SPY / KODEX200)": replace(cfg, us_universe=(US_INDEX,), kr_universe=(KR_INDEX,),
                                                      rank_weights=(100, 0, 0, 0, 0)),
        "전략, 수수료·세금·환전비용 0": replace(cfg, us_fee=0, kr_fee=0, kr_stock_sell_tax=0, fx_spread=0),
    }

    OUT_DIR.mkdir(exist_ok=True)
    table, curves, main_res = [], {}, None
    for name, c in scenarios.items():
        res = run(md, c, start, a.end, a.capital)
        if main_res is None:
            main_res = res
        s = res["curve"]["total_krw"]
        curves[name] = s
        m = metrics(s)
        table.append({"시나리오": name, "총수익률": m["total_return"], "CAGR": m["cagr"], "변동성": m["vol"],
                      "Sharpe(rf=0)": m["sharpe"], "MDD": m["mdd"], "최종자산(원)": s.iloc[-1],
                      "비용합계(원)": res["cost_krw"], "누적거래대금/초기자본": res["traded_krw"] / a.capital,
                      "순위변경(US/KR)": f"{res['rank_changes']['US']}/{res['rank_changes']['KR']}",
                      "매매일수(US/KR)": f"{len(res['trade_days']['US'])}/{len(res['trade_days']['KR'])}",
                      "환전횟수": res["fx_moves"]})

    # buy & hold references, KRW basis
    adj = md["adj"].ffill().loc[curves[next(iter(curves))].index]
    fx = adj[data.FX_TICKER]
    for name, s in {"SPY 단순보유 (원화환산)": adj[US_INDEX] * fx, "KODEX200 단순보유": adj[KR_INDEX],
                    "원/달러 환율": fx}.items():
        s = s / s.iloc[0] * a.capital
        curves[name] = s
        m = metrics(s)
        table.append({"시나리오": name, "총수익률": m["total_return"], "CAGR": m["cagr"], "변동성": m["vol"],
                      "Sharpe(rf=0)": m["sharpe"], "MDD": m["mdd"], "최종자산(원)": s.iloc[-1]})

    tbl = pd.DataFrame(table).set_index("시나리오")
    curve_df = pd.DataFrame(curves)
    curve_df.to_csv(OUT_DIR / "equity_curves.csv", encoding="utf-8-sig")
    main_res["curve"].to_csv(OUT_DIR / "strategy_daily.csv", encoding="utf-8-sig")
    main_res["events"].to_csv(OUT_DIR / "rank_events.csv", index=False, encoding="utf-8-sig")
    yr = pd.DataFrame({n: yearly(s) for n, s in curves.items()})

    pct = lambda v: f"{v:+.1%}" if pd.notna(v) else ""
    fmt = tbl.copy()
    for col in ("총수익률", "CAGR", "MDD"):
        fmt[col] = fmt[col].map(pct)
    fmt["변동성"] = fmt["변동성"].map(lambda v: f"{v:.1%}")
    fmt["Sharpe(rf=0)"] = fmt["Sharpe(rf=0)"].map(lambda v: f"{v:.2f}")
    for col in ("최종자산(원)", "비용합계(원)"):
        fmt[col] = fmt[col].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "")
    if "환전횟수" in fmt:
        fmt["환전횟수"] = fmt["환전횟수"].map(lambda v: f"{v:.0f}" if pd.notna(v) else "")
    fmt["누적거래대금/초기자본"] = fmt["누적거래대금/초기자본"].map(lambda v: f"{v:.1f}x" if pd.notna(v) else "")
    fmt = fmt.fillna("")

    cfg_view = {k: v for k, v in asdict(cfg).items() if k != "kr_taxable"}
    md_out = [f"# 백테스트 결과 {main_res['curve'].index[0].date()} ~ {main_res['curve'].index[-1].date()}", "",
              f"초기자본 {a.capital:,.0f}원, 원화 기준. 설정: `{cfg_view}`", "",
              fmt.to_markdown(), "", "## 연도별 수익률", "", yr.map(pct).to_markdown(), ""]
    (OUT_DIR / "summary.md").write_text("\n".join(md_out), encoding="utf-8")
    print("\n".join(md_out))
    if not main_res["events"].empty:
        print("\n## 확정 순위 변경 (메인 전략)\n")
        print(main_res["events"].to_markdown(index=False))


if __name__ == "__main__":
    main()
