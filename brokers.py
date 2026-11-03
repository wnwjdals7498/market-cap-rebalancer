"""Broker interface and the in-process simulator used for paper trading without a brokerage."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from strategy import CASH, Config, trade_cost


@dataclass(frozen=True)
class OrderState:
    order_no: str
    filled_qty: int
    avg_price: float | None
    open_qty: int  # still working at the exchange


class Broker(Protocol):
    name: str

    def balance(self, market: str) -> dict[str, float]: ...          # {ticker: qty, CASH: amount}
    def buying_power(self, market: str, ticker: str, price: float) -> int: ...  # max buyable qty
    def place(self, market: str, ticker: str, side: str, qty: int, price: float) -> str: ...
    def status(self, market: str, ticker: str, order_no: str) -> OrderState: ...
    def cancel(self, market: str, ticker: str, order_no: str, open_qty: int) -> None: ...


class SimBroker:
    """Fills a limit order immediately at the market price when the limit crosses it."""
    name = "sim"

    def __init__(self, positions: dict[str, dict[str, float]], prices: dict[str, float], cfg: Config):
        self.positions = {m: dict(p) for m, p in positions.items()}
        self.prices, self.cfg = prices, cfg
        self.orders: dict[str, OrderState] = {}
        self.costs: dict[str, float] = {}

    def balance(self, market: str) -> dict[str, float]:
        return dict(self.positions[market])

    def buying_power(self, market: str, ticker: str, price: float) -> int:
        cash = self.positions[market].get(CASH, 0.0)
        return max(math.floor(cash / (price + trade_cost(self.cfg, market, ticker, price))), 0)

    def place(self, market: str, ticker: str, side: str, qty: int, price: float) -> str:
        order_no = f"SIM{len(self.orders) + 1}"
        px = self.prices[ticker]
        crosses = price >= px if side == "BUY" else price <= px
        if not crosses:
            self.orders[order_no] = OrderState(order_no, 0, None, qty)
            return order_no
        signed = qty if side == "BUY" else -qty
        cost = trade_cost(self.cfg, market, ticker, signed * px)
        pos = self.positions[market]
        pos[ticker] = pos.get(ticker, 0) + signed
        pos[CASH] = pos.get(CASH, 0.0) - signed * px - cost
        if pos[ticker] == 0:
            del pos[ticker]
        self.orders[order_no] = OrderState(order_no, qty, px, 0)
        self.costs[order_no] = cost
        return order_no

    def status(self, market: str, ticker: str, order_no: str) -> OrderState:
        return self.orders[order_no]

    def cancel(self, market: str, ticker: str, order_no: str, open_qty: int) -> None:
        s = self.orders[order_no]
        self.orders[order_no] = OrderState(order_no, s.filled_qty, s.avg_price, 0)
