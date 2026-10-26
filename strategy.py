"""Strategy rules shared by the backtest and the daily rebalancer.

자산을 두 '포켓'으로 나눔: US(달러) / KR(원화). 각 포켓에서:

* 평상시(SOLO): 시총 1위 stock_pct(60) : 채권 ETF (100 - stock_pct)(40)
* 1위가 바뀌면 하루 최대 포켓 자산의 step_pct(10%)씩만 구 1위 -> 신 1위로 이동.
  확정 전에 다시 뒤집히면 같은 속도로 되돌림 (엎치락뒤치락 대응).
* 신 1위가 confirm_days(3)일 연속 유지되면 SPLIT: 1위 30 / 2위 30 / 채권 40.
  SPLIT 에서는 1·2위 순서만 바뀌면 거래 없음. 보유 2종목 밖 종목이 pair_confirm_days(3)일
  연속 top2 에 있으면 top2 에서 밀려난 보유 종목과 교체, 역시 step_pct 씩 이동.
* 주식 : 채권 비율 자체는 한도 없이 매일 맞춤 (step_pct 는 주식 종목 간 이동에만 적용).
* solo_after_days > 0 이면 같은 1위가 그 기간 유지될 때 다시 SOLO(1위 60)로 복귀.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CASH = "CASH"


@dataclass
class Config:
    stock_pct: float = 60.0           # 주식(시총 상위) 비중 (%). 나머지는 채권 ETF
    us_pct: float = 50.0              # 총자산 중 미국(달러) 포켓 비중 (%)
    fx_mode: str = "separate"         # separate: 달러/원화 각자 리밸런싱, combined: 환전해서 us_pct 까지 맞춤
    band_pp: float = 0.0              # 주식/채권 비중 허용 오차 (%p). 0 이면 매일 정확히 맞춤
    step_pct: float = 10.0            # 1위 교체 시 하루 최대 이동량 (포켓 자산 대비 %)
    confirm_days: int = 3             # 신 1위가 이 일수 연속 유지되면 1위/2위 반반(SPLIT)
    pair_confirm_days: int = 3        # SPLIT 에서 보유 2종목 밖 종목이 이 일수 연속 top2 면 교체. 0 = 즉시
    solo_after_days: int = 0          # SPLIT 에서 같은 1위가 이 일수 유지되면 다시 1위 단독. 0 = 복귀 안함
    us_bond: str = "IEF"              # 미국 7-10년 국채 ETF
    kr_bond: str = "148070.KS"        # KIWOOM 국고채10년
    us_fee: float = 0.0007            # 미국 주식/ETF 매매 수수료 (편도)
    kr_fee: float = 0.00015           # 국내 매매 수수료 (편도)
    kr_stock_sell_tax: float = 0.0020 # 국내 주식 매도 시 거래세 (ETF 면제)
    fx_spread: float = 0.0025         # 환전 스프레드 (편도)
    kr_taxable: frozenset = field(default_factory=frozenset)  # 거래세 대상 종목

    @property
    def stock_w(self) -> float:
        return self.stock_pct / 100


@dataclass
class LeaderState:
    """Per-pocket ranking memory. Serializable (asdict) for the live rebalancer."""
    mode: str = "SOLO"          # SOLO | SPLIT
    holding: str | None = None  # SOLO 모드에서 확정된 1위
    leader: str | None = None   # 직전 거래일 기준 1위
    streak: int = 0             # leader 가 연속으로 1위였던 일수
    pair: list[str] = field(default_factory=list)  # SPLIT 모드에서 반반 보유하는 2종목
    candidate: str | None = None  # pair 밖에서 top2 에 들어온 종목
    cand_streak: int = 0
    switches: int = 0           # 1위 변경 횟수 (통계용)


def update_state(state: LeaderState, ranked: list[str], cfg: Config) -> dict[str, float]:
    """Advance the state with today's ranking (desc by market cap) and return
    stock-sleeve weights (sum = 1) to steer toward."""
    top = ranked[0]
    if top == state.leader:
        state.streak += 1
    else:
        if state.leader is not None:
            state.switches += 1
        state.leader, state.streak = top, 1

    if state.holding is None:  # first day
        state.holding = top

    if state.mode == "SOLO" and top != state.holding and state.streak >= cfg.confirm_days:
        state.mode, state.pair = "SPLIT", [top, state.holding]
        state.candidate, state.cand_streak = None, 0
    elif state.mode == "SPLIT" and cfg.solo_after_days > 0 and state.streak >= cfg.solo_after_days:
        state.mode, state.holding, state.pair = "SOLO", top, []

    if state.mode == "SPLIT":
        _update_pair(state, ranked[:2], cfg)
        return {t: 0.5 for t in state.pair}
    # SOLO: steer toward today's #1. Before confirmation this is capped by step_pct,
    # so a 1-2 day blip only moves 10-20% and is walked back if it reverses.
    return {top: 1.0}


def _update_pair(state: LeaderState, top2: list[str], cfg: Config) -> None:
    """Swap a pair member out only after an outsider stays in the top 2 for pair_confirm_days."""
    outsiders = [t for t in top2 if t not in state.pair]
    if not outsiders:
        state.candidate, state.cand_streak = None, 0
        return
    new = outsiders[0]
    if new == state.candidate:
        state.cand_streak += 1
    else:
        state.candidate, state.cand_streak = new, 1
    if state.cand_streak >= cfg.pair_confirm_days:
        dropped = next(t for t in reversed(state.pair) if t not in top2)
        state.pair = [new if t == dropped else t for t in state.pair]
        state.candidate, state.cand_streak = None, 0


def trade_cost(cfg: Config, market: str, ticker: str, delta_value: float) -> float:
    """Cost (pocket currency) of buying (+) or selling (-) `delta_value` of `ticker`."""
    if market == "US":
        return abs(delta_value) * cfg.us_fee
    cost = abs(delta_value) * cfg.kr_fee
    if delta_value < 0 and ticker in cfg.kr_taxable:
        cost += -delta_value * cfg.kr_stock_sell_tax
    return cost


def pocket_value(units: dict[str, float], prices: dict[str, float]) -> float:
    return sum(u * (1.0 if t == CASH else prices[t]) for t, u in units.items())


def plan_targets(units: dict[str, float], prices: dict[str, float], stock_weights: dict[str, float],
                 bond: str, cfg: Config, investable: float) -> dict[str, float]:
    """Target values: bond sleeve at (1 - stock_w), stock sleeve moved toward
    `stock_weights` but at most step_pct of the pocket per day."""
    value = pocket_value(units, prices)
    held = {t: u * prices[t] for t, u in units.items() if t not in (CASH, bond) and u > 0}
    sleeve = investable * cfg.stock_w
    held_total = sum(held.values())

    # current composition rescaled to the target sleeve size (the 60/40 resize itself is uncapped)
    cur = {t: v / held_total * sleeve for t, v in held.items()} if held_total > 0 else {}
    want = {t: w * sleeve for t, w in stock_weights.items()}
    names = set(cur) | set(want)
    outflow = sum(max(cur.get(t, 0.0) - want.get(t, 0.0), 0.0) for t in names)
    cap = cfg.step_pct / 100 * value
    alpha = 1.0 if not cur or outflow <= cap else cap / outflow

    target = {t: cur.get(t, 0.0) + alpha * (want.get(t, 0.0) - cur.get(t, 0.0)) for t in names}
    target = {t: v for t, v in target.items() if v > 1e-9}
    target[bond] = investable * (1 - cfg.stock_w)
    return target


def needs_rebalance(units: dict[str, float], prices: dict[str, float], stock_weights: dict[str, float],
                    bond: str, cfg: Config) -> bool:
    value = pocket_value(units, prices)
    if value <= 0:
        return False
    target = plan_targets(units, prices, stock_weights, bond, cfg, value)
    held = {t: u * prices[t] for t, u in units.items() if t != CASH}
    names = set(target) | set(held)
    if cfg.band_pp == 0:
        return any(abs(target.get(t, 0.0) - held.get(t, 0.0)) / value > 1e-6 for t in names)
    # with a band: only composition changes (leader moves) or sleeve drift beyond band trigger trades
    stocks_now = {t for t, v in held.items() if t != bond and v > 0}
    stocks_tgt = {t for t in target if t != bond}
    w_bond = held.get(bond, 0.0) / value
    shifting = any(abs(target.get(t, 0.0) - held.get(t, 0.0)) / value > cfg.band_pp / 100
                   for t in stocks_now | stocks_tgt)
    return stocks_now != stocks_tgt or shifting or abs(w_bond - (1 - cfg.stock_w)) > cfg.band_pp / 100 \
        or abs(units.get(CASH, 0.0)) / value > 1e-6


def rebalance_pocket(units: dict[str, float], prices: dict[str, float], stock_weights: dict[str, float],
                     bond: str, market: str, cfg: Config) -> tuple[dict[str, float], float, float]:
    """New units for one pocket. Returns (new_units, cost, traded_notional).

    Costs are paid out of the pocket, so targets are sized on (value - cost);
    a few fixed-point passes settle that.
    """
    value = pocket_value(units, prices)
    current = {t: u * prices[t] for t, u in units.items() if t != CASH}
    cost = 0.0
    for _ in range(4):
        target = plan_targets(units, prices, stock_weights, bond, cfg, value - cost)
        deltas = {t: target.get(t, 0.0) - current.get(t, 0.0) for t in set(target) | set(current)}
        cost = sum(trade_cost(cfg, market, t, d) for t, d in deltas.items())
    new_units = {t: v / prices[t] for t, v in target.items()}
    new_units[CASH] = value - cost - sum(target.values())  # ~0, rounding residual
    return new_units, cost, sum(abs(d) for d in deltas.values())
