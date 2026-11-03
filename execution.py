"""Order execution: limit-price ladder within +/-5% of the reference price, sells before buys."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from brokers import Broker, OrderState

log = logging.getLogger("mcr")

# Limit price offsets from the reference price, tried in order. The first rung is the
# reference itself; each later rung is more aggressive, never past PRICE_BAND.
LADDER = (0.0, 0.005, 0.01, 0.02, 0.035, 0.05)
PRICE_BAND = 0.05


@dataclass(frozen=True)
class ExecParams:
    ladder: tuple[float, ...] = LADDER
    step_wait: float = 60.0   # seconds an order may rest before it is cancelled and re-priced
    poll: float = 10.0        # status polling interval


@dataclass
class TickerResult:
    ticker: str
    side: str
    wanted: int
    filled: int = 0
    notional: float = 0.0
    orders: list[dict] = field(default_factory=list)  # one per rung, for the orders table

    @property
    def avg_price(self) -> float | None:
        return self.notional / self.filled if self.filled else None


def kr_tick(price: float, etf: bool) -> int:
    """KRX tick size (2023 unified table). ETFs/ETNs: 1 won under 2,000, else 5."""
    if etf:
        return 1 if price < 2000 else 5
    for limit, tick in ((2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)):
        if price < limit:
            return tick
    return 1000


def round_to_tick(price: float, market: str, side: str, etf: bool = False) -> float:
    """Buys round down and sells round up, so the band is never exceeded."""
    if market == "KR":
        step = Decimal(kr_tick(price, etf))
    else:
        step = Decimal("0.01") if price >= 1 else Decimal("0.0001")
    rounding = ROUND_FLOOR if side == "BUY" else ROUND_CEILING
    exact = Decimal(str(round(price, 6)))  # drop float noise (100 * 1.005 = 100.4999...) before flooring
    return float((exact / step).to_integral_value(rounding) * step)


def limit_price(ref: float, offset: float, market: str, side: str, etf: bool = False) -> float:
    offset = min(offset, PRICE_BAND)
    raw = ref * (1 + offset) if side == "BUY" else ref * (1 - offset)
    return round_to_tick(raw, market, side, etf)


def _wait_fill(broker: Broker, market: str, ticker: str, order_no: str, qty: int, p: ExecParams,
               sleep) -> OrderState:
    waited = 0.0
    state = broker.status(market, ticker, order_no)
    while state.open_qty > 0 and waited < p.step_wait:
        sleep(p.poll)
        waited += p.poll
        state = broker.status(market, ticker, order_no)
    return state


def execute(broker: Broker, market: str, plan: list[tuple[str, int]], refs: dict[str, float],
            etfs: set[str] = frozenset(), params: ExecParams = ExecParams(), sleep=time.sleep) -> list[TickerResult]:
    """plan: [(ticker, signed qty)]. Sells run first so their proceeds can fund the buys."""
    results = []
    for ticker, delta in sorted(plan, key=lambda o: o[1]):
        side = "BUY" if delta > 0 else "SELL"
        res = TickerResult(ticker, side, abs(delta))
        results.append(res)
        for rung, offset in enumerate(params.ladder):
            remaining = res.wanted - res.filled
            if remaining <= 0:
                break
            price = limit_price(refs[ticker], offset, market, side, ticker in etfs)
            qty = remaining if side == "SELL" else min(remaining, broker.buying_power(market, ticker, price))
            if qty <= 0:
                log.warning("%s %s: no buying power at %s", market, ticker, price)
                break
            order_no = broker.place(market, ticker, side, qty, price)
            state = _wait_fill(broker, market, ticker, order_no, qty, params, sleep)
            if state.open_qty > 0:
                broker.cancel(market, ticker, order_no, state.open_qty)
                state = broker.status(market, ticker, order_no)  # fills may land before the cancel
            res.filled += state.filled_qty
            res.notional += state.filled_qty * (state.avg_price or price)
            res.orders.append({"step": rung, "qty": qty, "limit_price": price, "ref_price": refs[ticker],
                               "broker_order_no": order_no, "filled": state.filled_qty,
                               "fill_price": state.avg_price,
                               "status": "FILLED" if state.filled_qty == qty
                               else "PARTIAL" if state.filled_qty else "CANCELLED"})
        log.info("%s %s %s %d/%d avg %s", market, side, ticker, res.filled, res.wanted, res.avg_price)
    return results
