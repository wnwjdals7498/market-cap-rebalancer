"""Daily rebalancer (paper account). Run once a day; prints orders, --apply fills them.

    python rebalance.py --init-usd 35000 --init-krw 50000000   # create paper account
    python rebalance.py                                         # show today's orders
    python rebalance.py --apply                                 # fill at last close, save state

Ranking uses the latest completed close of each market; orders are meant to be
executed in the next session (same 1-day lag as the backtest).
Real brokerage: implement `Broker` (e.g. 한국투자증권 Open API) and pass it to `run`.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, fields
from pathlib import Path
from typing import Protocol

import pandas as pd

import data
from strategy import CASH, Config, LeaderState, needs_rebalance, pocket_value, rebalance_pocket, update_state

STATE_FILE = Path(__file__).parent / "paper_state.json"
UNIVERSE = {"US": data.US_UNIVERSE, "KR": data.KR_UNIVERSE}


class Broker(Protocol):
    def positions(self) -> dict[str, dict[str, float]]: ...       # {"US": {ticker: qty, "CASH": usd}, "KR": {...}}
    def place_order(self, market: str, ticker: str, qty: int, price: float) -> None: ...  # qty<0 = sell


class PaperBroker:
    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        self.state = json.loads(path.read_text(encoding="utf-8"))

    def positions(self) -> dict[str, dict[str, float]]:
        return self.state["positions"]

    def place_order(self, market: str, ticker: str, qty: int, price: float) -> None:
        pos = self.state["positions"][market]
        pos[ticker] = pos.get(ticker, 0) + qty
        pos[CASH] -= qty * price  # paper fill at last close, fees ignored
        if pos[ticker] == 0:
            del pos[ticker]

    def save(self) -> None:
        self.path.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")


def latest_signals(cfg: Config) -> tuple[dict[str, list[str]], dict[str, pd.Timestamp], dict[str, float], float]:
    start = str((pd.Timestamp.today() - pd.Timedelta(days=120)).date())
    md = data.load_market_data(start, extra=[cfg.us_bond, cfg.kr_bond], refresh=True)
    close, mcap = md["close"], md["mcap"]
    ranked, asof = {}, {}
    for m, uni in UNIVERSE.items():
        row = mcap[uni].dropna(how="all").iloc[-1]
        asof[m] = mcap[uni].dropna(how="all").index[-1]
        ranked[m] = list(row.dropna().sort_values(ascending=False).index)
    prices = close.ffill().iloc[-1].to_dict()
    return ranked, asof, prices, prices[data.FX_TICKER]


def run(broker: Broker, saved: dict, cfg: Config, apply: bool) -> None:
    ranked, asof, prices, usdkrw = latest_signals(cfg)
    bond = {"US": cfg.us_bond, "KR": cfg.kr_bond}
    positions = broker.positions()
    total = 0.0

    for m in ("US", "KR"):
        st = LeaderState(**saved["leader_state"].get(m, {}))
        day = str(asof[m].date())
        if saved["last_signal"].get(m) != day:  # one state step per market day; re-runs are idempotent
            weights = update_state(st, ranked[m], cfg)
            if apply:
                saved["leader_state"][m], saved["last_signal"][m] = asdict(st), day
        else:
            weights = {t: 0.5 for t in st.pair} if st.mode == "SPLIT" else {st.leader: 1.0}

        units = {t: float(q) for t, q in positions[m].items()}
        value = pocket_value(units, prices)
        total += value * (usdkrw if m == "US" else 1)
        ccy = "USD" if m == "US" else "KRW"
        print(f"\n[{m}] 기준일 {day}  순위 {ranked[m][:3]}  모드 {st.mode}  목표주식 {weights}")
        print(f"  평가액 {value:,.2f} {ccy}  보유 {positions[m]}")

        if not needs_rebalance(units, prices, weights, bond[m], cfg):
            print("  리밸런싱 불필요")
            continue
        target, _, _ = rebalance_pocket(units, prices, weights, bond[m], m, cfg)
        orders = []
        for t in sorted(set(target) | set(units)):
            if t == CASH:
                continue
            delta = target.get(t, 0.0) - units.get(t, 0.0)
            qty = math.floor(delta) if delta > 0 else -math.floor(-delta + 1e-9) if target.get(t, 0) > 0 \
                else -round(units.get(t, 0.0))  # full exit when dropped
            if qty:
                orders.append((t, qty))
        if not orders:
            print("  주문 없음 (1주 미만 차이)")
        # sells first so their cash funds the buys
        for t, qty in sorted(orders, key=lambda o: o[1]):
            print(f"  {'매도' if qty < 0 else '매수'} {t:10s} {abs(qty):>8,d}주 @ {prices[t]:,.2f}"
                  f"  ≈ {abs(qty) * prices[t]:,.0f} {ccy}")
            if apply:
                broker.place_order(m, t, qty, prices[t])

    print(f"\n총자산 ≈ {total:,.0f} KRW (USD/KRW {usdkrw:,.2f})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init-usd", type=float, help="create paper account with this USD cash")
    ap.add_argument("--init-krw", type=float, help="create paper account with this KRW cash")
    ap.add_argument("--apply", action="store_true", help="fill orders and save state")
    ap.add_argument("--stock-pct", type=float, help="override stock ratio (%%), saved into state")
    a = ap.parse_args()

    if a.init_usd is not None or a.init_krw is not None:
        state = {"config": {}, "leader_state": {}, "last_signal": {},
                 "positions": {"US": {CASH: a.init_usd or 0.0}, "KR": {CASH: a.init_krw or 0.0}}}
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(f"paper account created: {STATE_FILE}")

    broker = PaperBroker()
    saved = broker.state
    if a.stock_pct is not None:
        saved["config"]["stock_pct"] = a.stock_pct
    known = {f.name for f in fields(Config)}
    cfg = Config(**{k: v for k, v in saved["config"].items() if k in known},
                 kr_taxable=frozenset(data.KR_UNIVERSE))
    run(broker, saved, cfg, a.apply)
    if a.apply:
        broker.save()
        print("state saved")


if __name__ == "__main__":
    main()
