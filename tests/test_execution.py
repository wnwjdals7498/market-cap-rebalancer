import pytest

from brokers import OrderState, SimBroker
from execution import ExecParams, execute, kr_tick, limit_price, round_to_tick
from strategy import CASH, Config

FAST = ExecParams(step_wait=20, poll=10)


def test_kr_tick_table():
    assert [kr_tick(p, etf=False) for p in (1999, 4999, 19990, 49950, 199900, 499500, 500000)] == \
        [1, 5, 10, 50, 100, 500, 1000]
    assert (kr_tick(1500, etf=True), kr_tick(108100, etf=True)) == (1, 5)


def test_rounding_never_crosses_the_band():
    assert round_to_tick(218_730, "KR", "BUY") == 218_500     # buy rounds down
    assert round_to_tick(218_730, "KR", "SELL") == 219_000    # sell rounds up
    assert round_to_tick(108_103, "KR", "BUY", etf=True) == 108_100
    assert round_to_tick(338.327, "US", "BUY") == 338.32
    assert round_to_tick(338.321, "US", "SELL") == 338.33
    assert limit_price(100.0, 0.20, "US", "BUY") == 105.0       # offset capped at 5%
    assert limit_price(100.0, 0.20, "US", "SELL") == 95.0


class ScriptedBroker:
    """Each placed order fills `fills.pop(0)` shares (0 = rests), at the limit price."""
    name = "scripted"

    def __init__(self, fills, cash=1e9, late_fill=0):
        self.fills, self.cash, self.late_fill = list(fills), cash, late_fill
        self.placed, self.cancelled, self.orders = [], [], {}

    def balance(self, market):
        return {CASH: self.cash}

    def buying_power(self, market, ticker, price):
        return int(self.cash // price)

    def place(self, market, ticker, side, qty, price):
        no = f"O{len(self.placed)}"
        self.placed.append((ticker, side, qty, price))
        f = min(self.fills.pop(0) if self.fills else 0, qty)
        self.orders[no] = OrderState(no, f, price if f else None, qty - f)
        return no

    def status(self, market, ticker, no):
        return self.orders[no]

    def cancel(self, market, ticker, no, open_qty):
        s = self.orders[no]
        extra = min(self.late_fill, s.open_qty)  # fills that land while the cancel is in flight
        self.orders[no] = OrderState(no, s.filled_qty + extra, s.avg_price or 1.0, 0)
        self.cancelled.append(no)


def test_ladder_reprices_unfilled_remainder_until_done():
    b = ScriptedBroker(fills=[0, 3, 7])
    [r] = execute(b, "US", [("AAPL", 10)], {"AAPL": 100.0}, params=FAST, sleep=lambda s: None)
    assert [(q, p) for _, _, q, p in b.placed] == [(10, 100.0), (10, 100.5), (7, 101.0)]
    assert (r.filled, [o["status"] for o in r.orders]) == (10, ["CANCELLED", "PARTIAL", "FILLED"])
    assert b.cancelled == ["O0", "O1"]


def test_sells_first_and_stop_at_band():
    b = ScriptedBroker(fills=[5] + [0] * 6)
    res = execute(b, "KR", [("005930.KS", 3), ("000660.KS", -5)], {"005930.KS": 218_500, "000660.KS": 1_388_000},
                  params=FAST, sleep=lambda s: None)
    assert [p[:2] for p in b.placed][:2] == [("000660.KS", "SELL"), ("005930.KS", "BUY")]
    buy = res[1]
    assert buy.filled == 0 and len(buy.orders) == 6
    assert max(o["limit_price"] for o in buy.orders) <= 218_500 * 1.05


def test_buy_limited_by_buying_power_and_late_fill_counted():
    b = ScriptedBroker(fills=[0], cash=250.0, late_fill=1)
    [r] = execute(b, "US", [("IEF", 5)], {"IEF": 100.0}, params=ExecParams(ladder=(0.0,), step_wait=10, poll=10),
                  sleep=lambda s: None)
    assert b.placed == [("IEF", "BUY", 2, 100.0)]
    assert (r.filled, r.orders[0]["status"]) == (1, "PARTIAL")


def test_sim_broker_fills_at_market_and_charges_costs():
    cfg = Config(kr_fee=0.001, kr_stock_sell_tax=0.002, kr_taxable=frozenset({"005930.KS"}))
    b = SimBroker({"KR": {CASH: 1_000_000.0, "005930.KS": 10}}, {"005930.KS": 200_000.0}, cfg)
    assert b.status("KR", "005930.KS", b.place("KR", "005930.KS", "BUY", 1, 199_000)).filled_qty == 0
    b.place("KR", "005930.KS", "SELL", 2, 199_000)
    assert b.balance("KR") == {CASH: pytest.approx(1_000_000 + 400_000 - 400_000 * 0.003), "005930.KS": 8}
