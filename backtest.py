"""5-year backtest of the market-cap-leader / bond-ETF rebalancing strategy.

    python backtest.py                      # default: 60/40, 10%/day, 3-day confirm
    python backtest.py --stock-pct 70 --step-pct 20 --confirm-days 5
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

import data
from strategy import CASH, Config, LeaderState, needs_rebalance, pocket_value, rebalance_pocket, update_state

OUT_DIR = Path(__file__).parent / "results"
US_INDEX, KR_INDEX = "SPY", "069500.KS"  # S&P500, KODEX 200


def run(md: dict[str, pd.DataFrame], cfg: Config, start: str, end: str | None,
        us_universe: list[str], kr_universe: list[str], capital_krw: float = 100_000_000) -> dict:
    close, adj, mcap = md["close"], md["adj"].ffill(), md["mcap"]
    fx = adj[data.FX_TICKER]
    open_ = {"US": close[US_INDEX].notna(), "KR": close[KR_INDEX].notna()}
    # signal: ranking as of the previous close of that market (no look-ahead)
    lagged = {m: mcap.reindex(columns=u).where(open_[m], axis=0).ffill().shift(1)
              for m, u in (("US", us_universe), ("KR", kr_universe))}
    universe = {"US": us_universe, "KR": kr_universe}
    bond = {"US": cfg.us_bond, "KR": cfg.kr_bond}

    days = close.loc[start:end].index
    days = days[(open_["US"] | open_["KR"]).loc[days]]
    rate0 = fx.loc[:days[0]].iloc[-1]
    pockets = {"US": {CASH: capital_krw * cfg.us_pct / 100 / rate0},
               "KR": {CASH: capital_krw * (1 - cfg.us_pct / 100)}}
    states = {m: LeaderState() for m in pockets}
    cost_krw = traded_krw = 0.0
    rows, events = [], []

    for d in days:
        rate = fx.loc[d]
        prices = adj.loc[d].to_dict()
        is_open = {m: bool(open_[m].loc[d]) for m in pockets}

        if cfg.fx_mode == "combined" and all(is_open.values()):
            us_krw = pocket_value(pockets["US"], prices) * rate
            total = us_krw + pocket_value(pockets["KR"], prices)
            gap = total * cfg.us_pct / 100 - us_krw  # KRW to move into US (+) / out (-)
            if abs(gap) / total > max(cfg.band_pp / 100, 1e-9):
                fee = abs(gap) * cfg.fx_spread
                if gap > 0:
                    pockets["KR"][CASH] = pockets["KR"].get(CASH, 0.0) - gap
                    pockets["US"][CASH] = pockets["US"].get(CASH, 0.0) + (gap - fee) / rate
                else:
                    pockets["US"][CASH] = pockets["US"].get(CASH, 0.0) + gap / rate
                    pockets["KR"][CASH] = pockets["KR"].get(CASH, 0.0) - gap - fee
                cost_krw += fee
                traded_krw += abs(gap)

        for m, units in pockets.items():
            if not is_open[m]:
                continue
            if len(universe[m]) == 1:  # fixed holding (e.g. index benchmark), no ranking needed
                ranked = universe[m]
            else:
                row = lagged[m].loc[d].dropna()
                if row.empty:  # need at least one signal day
                    continue
                ranked = list(row.sort_values(ascending=False).index)
            st = states[m]
            prev = (st.mode, st.leader, tuple(st.pair))
            weights = update_state(st, ranked, cfg)
            if prev != (st.mode, st.leader, tuple(st.pair)) and prev[1] is not None:
                events.append({"date": d.date(), "market": m, "rank1": st.leader,
                               "rank2": ranked[1] if len(ranked) > 1 else "", "mode": st.mode,
                               "target": "/".join(weights)})
            if needs_rebalance(units, prices, weights, bond[m], cfg):
                new, cost, traded = rebalance_pocket(units, prices, weights, bond[m], m, cfg)
                pockets[m] = new
                k = rate if m == "US" else 1.0
                cost_krw += cost * k
                traded_krw += traded * k

        us_val = pocket_value(pockets["US"], prices)
        kr_val = pocket_value(pockets["KR"], prices)
        hold = {f"{m}:{t}": u * prices.get(t, 1.0) * (rate if m == "US" else 1.0)
                for m, p in pockets.items() for t, u in p.items() if t != CASH and u > 1e-12}
        rows.append({"date": d, "total_krw": us_val * rate + kr_val, "us_usd": us_val, "kr_krw": kr_val,
                     "usdkrw": rate, **hold})

    curve = pd.DataFrame(rows).set_index("date")
    return {"curve": curve, "cost_krw": cost_krw, "traded_krw": traded_krw,
            "switches": {m: s.switches for m, s in states.items()}, "events": pd.DataFrame(events)}


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    defaults = Config()
    ap.add_argument("--start", default=None, help="default: 5 years before --end/today")
    ap.add_argument("--end", default=None)
    ap.add_argument("--capital", type=float, default=100_000_000, help="initial capital, KRW")
    for name in ("stock_pct", "us_pct", "band_pp", "step_pct"):
        ap.add_argument("--" + name.replace("_", "-"), type=float, default=getattr(defaults, name))
    for name in ("confirm_days", "pair_confirm_days", "solo_after_days"):
        ap.add_argument("--" + name.replace("_", "-"), type=int, default=getattr(defaults, name))
    ap.add_argument("--fx-mode", choices=["separate", "combined"], default=defaults.fx_mode)
    ap.add_argument("--us-bond", default=defaults.us_bond)
    ap.add_argument("--kr-bond", default=defaults.kr_bond)
    ap.add_argument("--no-cost", action="store_true", help="zero fees/taxes/fx spread")
    ap.add_argument("--refresh", action="store_true", help="re-download data")
    a = ap.parse_args()

    end_ts = pd.Timestamp(a.end) if a.end else pd.Timestamp.today().normalize()
    start = a.start or str((end_ts - pd.DateOffset(years=5)).date())
    fetch_start = str((pd.Timestamp(start) - pd.DateOffset(days=40)).date())

    cfg = Config(stock_pct=a.stock_pct, us_pct=a.us_pct, band_pp=a.band_pp, step_pct=a.step_pct,
                 confirm_days=a.confirm_days, pair_confirm_days=a.pair_confirm_days,
                 solo_after_days=a.solo_after_days, fx_mode=a.fx_mode,
                 us_bond=a.us_bond, kr_bond=a.kr_bond, kr_taxable=frozenset(data.KR_UNIVERSE))
    if a.no_cost:
        cfg = replace(cfg, us_fee=0, kr_fee=0, kr_stock_sell_tax=0, fx_spread=0)

    md = data.load_market_data(fetch_start, a.end, extra=[cfg.us_bond, cfg.kr_bond, US_INDEX, KR_INDEX],
                               refresh=a.refresh)

    U, K = data.US_UNIVERSE, data.KR_UNIVERSE
    never = 10**9
    scenarios = {
        "전략 (설정값)": (cfg, U, K),
        "전략, 2위 교체 확정 없이 즉시 10%씩": (replace(cfg, pair_confirm_days=0), U, K),
        "전략 + 20일 유지 시 1위 단독 복귀": (replace(cfg, solo_after_days=20), U, K),
        "전략, 주식/채권 밴드 ±5%p": (replace(cfg, band_pp=5), U, K),
        "단순 교체 (1위 바뀌면 즉시 100% 이동)": (replace(cfg, step_pct=100, confirm_days=never), U, K),
        "같은 60/40, 지수 (SPY / KODEX200)": (cfg, [US_INDEX], [KR_INDEX]),
        "전략, 수수료·세금·환전비용 0": (replace(cfg, us_fee=0, kr_fee=0, kr_stock_sell_tax=0, fx_spread=0), U, K),
    }
    if cfg.fx_mode == "separate":
        scenarios["전략, 환전 포함 50:50 유지 (combined)"] = (replace(cfg, fx_mode="combined"), U, K)

    OUT_DIR.mkdir(exist_ok=True)
    table, curves, main_res = [], {}, None
    for name, (c, us_u, kr_u) in scenarios.items():
        res = run(md, c, start, a.end, us_u, kr_u, a.capital)
        if main_res is None:
            main_res = res
        s = res["curve"]["total_krw"]
        curves[name] = s
        m = metrics(s)
        table.append({"시나리오": name, "총수익률": m["total_return"], "CAGR": m["cagr"], "변동성": m["vol"],
                      "Sharpe(rf=0)": m["sharpe"], "MDD": m["mdd"], "최종자산(원)": s.iloc[-1],
                      "비용합계(원)": res["cost_krw"], "누적거래대금/초기자본": res["traded_krw"] / a.capital,
                      "1위교체(US/KR)": f"{res['switches']['US']}/{res['switches']['KR']}"})

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
    main_res["events"].to_csv(OUT_DIR / "leader_events.csv", index=False, encoding="utf-8-sig")
    yr = pd.DataFrame({n: yearly(s) for n, s in curves.items()})

    pct = lambda v: f"{v:+.1%}" if pd.notna(v) else ""
    fmt = tbl.copy()
    for col in ("총수익률", "CAGR", "MDD"):
        fmt[col] = fmt[col].map(pct)
    fmt["변동성"] = fmt["변동성"].map(lambda v: f"{v:.1%}")
    fmt["Sharpe(rf=0)"] = fmt["Sharpe(rf=0)"].map(lambda v: f"{v:.2f}")
    for col in ("최종자산(원)", "비용합계(원)"):
        fmt[col] = fmt[col].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "")
    fmt["누적거래대금/초기자본"] = fmt["누적거래대금/초기자본"].map(lambda v: f"{v:.1f}x" if pd.notna(v) else "")
    fmt = fmt.fillna("")

    cfg_view = {k: v for k, v in asdict(cfg).items() if k != "kr_taxable"}
    md_out = [f"# 백테스트 결과 {main_res['curve'].index[0].date()} ~ {main_res['curve'].index[-1].date()}", "",
              f"초기자본 {a.capital:,.0f}원, 원화 기준. 설정: `{cfg_view}`", "",
              fmt.to_markdown(), "", "## 연도별 수익률", "", yr.map(pct).to_markdown(), ""]
    (OUT_DIR / "summary.md").write_text("\n".join(md_out), encoding="utf-8")
    print("\n".join(md_out))
    if not main_res["events"].empty:
        print("\n## 1위/모드 변경 (메인 전략)\n")
        print(main_res["events"].to_markdown(index=False))


if __name__ == "__main__":
    main()
