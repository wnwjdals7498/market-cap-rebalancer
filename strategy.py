"""Strategy rules shared by the backtest and the daily rebalancer.

자산을 두 '포켓'으로 나눔: US(달러) / KR(원화). 각 포켓에서:

* 시장별 후보 풀(pool_size 종목)을 시총 순으로 줄 세운다 (update_ranking: confirm_days 만큼 유지돼야 순위 변경 확정).
* 주식 몫(stock_pct) 안에서 1~5위에 rank_weights 비율을 준다 (stock_weights). 데이터 없는 종목은
  순위에서 빠지고 남은 순위 비율을 다시 정규화한다.
* 나머지 채권 몫은 short_bond_pct 로 단기채/장기채에 나눈다 (pocket_weights).
* 매매는 trade_every(일/주/월) 주기의 첫 성공 거래일에만, 1회에 매도 합계와 매수 합계가 각각
  step_pct × 포켓 자산을 넘지 않게 목표로 이동한다 (plan_moves). 포켓에 보유가 없을 때의 첫 매수는 예외.
* 미국/한국 비율이 us_pct ± fx_band_pp 를 벗어나면 환전 금액을 계산해 안내한다 (fx_gap, fx_amount).
  환전은 사용자가 직접 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

CASH = "CASH"


@dataclass
class Config:
    stock_pct: float = 60.0           # 주식(시총 상위) 비중 (%). 나머지는 채권 ETF
    us_pct: float = 60.0              # 총자산 중 미국(달러) 포켓 비중 (%)
    band_pp: float = 0.0              # 주식/채권 비중 허용 오차 (%p). 0 이면 매일 정확히 맞춤
    step_pct: float = 10.0            # 매매 1회 최대 이동량 (포켓 자산 대비 %)
    confirm_days: int = 0             # 순위 변경을 확정하기까지 대기하는 거래일 수
    us_fee: float = 0.0007            # 미국 주식/ETF 매매 수수료 (편도)
    kr_fee: float = 0.00015           # 국내 매매 수수료 (편도)
    kr_stock_sell_tax: float = 0.0020 # 국내 주식 매도 시 거래세 (ETF 면제)
    fx_spread: float = 0.0025         # 환전 스프레드 (편도)
    min_trade_pct: float = 0.5        # 실거래: 포켓 대비 이보다 작은 주문은 생략 (전량 매도는 예외)
    kr_taxable: frozenset = field(default_factory=frozenset)  # 거래세 대상 종목
    rank_weights: tuple[float, ...] = (50, 30, 20, 0, 0)  # 순위별 주식 비율 (%)
    short_bond_pct: float = 100.0     # 채권 중 단기채 비율 (%)
    trade_every: str = "week"         # 매매 주기
    fx_band_pp: float = 5.0           # 미국 비중 환전 허용 오차 (%p)
    fx_enabled: bool = True          # 환전 계획 사용 여부
    fx_wait_days: int = 3             # 환전 대기 일수
    pool_size: int = 15               # 시장별 후보 풀 크기 (종목 수)
    pool_auto: bool = True            # 후보 풀을 매주 자동 갱신 (끄면 수동 목록 유지)
    # 후보 풀: screener.screen_top("US"/"KR", 15) 결과, 2026-11-10 11:28 KST 조회 (시총 순)
    us_universe: tuple[str, ...] = (
        "AAPL", "NVDA", "MSFT", "GOOG", "AMZN", "TSM", "SPCX", "META", "AVGO", "TSLA",
        "LLY", "BRK-B", "WMT", "SKHY", "JPM")
    kr_universe: tuple[str, ...] = (
        "005930.KS", "000660.KS", "402340.KS", "373220.KS", "005380.KS", "207940.KS", "009150.KS",
        "105560.KS", "068270.KS", "028260.KS", "032830.KS", "055550.KS", "000270.KS", "006400.KS",
        "012450.KS")
    us_short_bond: str = "SHY"        # 미국 단기채
    us_long_bond: str = "TLT"         # 미국 장기채
    kr_short_bond: str = "114260.KS"  # 한국 단기채
    kr_long_bond: str = "385560.KS"   # 한국 장기채

    def universe(self, market: str) -> tuple[str, ...]:
        return self.us_universe if market == "US" else self.kr_universe

    def bonds(self, market: str) -> tuple[str, str]:
        return ((self.us_short_bond, self.us_long_bond) if market == "US"
                else (self.kr_short_bond, self.kr_long_bond))

    @property
    def stock_w(self) -> float:
        return self.stock_pct / 100


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


@dataclass
class RankState:
    confirmed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    streak: int = 0


def update_ranking(state: RankState, ranked: list[str], cfg: Config) -> list[str]:
    """Confirm ranking changes after the configured number of sessions."""
    if cfg.confirm_days == 0 or not state.confirmed or set(ranked) != set(state.confirmed):
        state.confirmed = ranked.copy()
        state.pending, state.streak = [], 0
    elif ranked == state.confirmed:
        state.pending, state.streak = [], 0
    else:
        if ranked == state.pending:
            state.streak += 1
        else:
            state.pending, state.streak = ranked.copy(), 1
        if state.streak >= cfg.confirm_days:
            state.confirmed = ranked.copy()
            state.pending, state.streak = [], 0
    return state.confirmed.copy()


def stock_weights(ranking: list[str], cfg: Config) -> dict[str, float]:
    """Normalize the configured weights for available ranked stocks."""
    pairs = list(zip(ranking, cfg.rank_weights))
    total = sum(weight for _, weight in pairs)
    return {ticker: weight / total for ticker, weight in pairs if weight > 0} if total > 0 else {}


def pocket_weights(stock_w: dict[str, float], market: str, cfg: Config) -> dict[str, float]:
    """Allocate the pocket between stocks and short- and long-term bonds."""
    stock_share = cfg.stock_w if stock_w else 0.0
    short, long = cfg.bonds(market)
    weights = {ticker: weight * stock_share for ticker, weight in stock_w.items()}
    weights[short] = (1 - stock_share) * cfg.short_bond_pct / 100
    weights[long] = (1 - stock_share) * (1 - cfg.short_bond_pct / 100)
    return {ticker: weight for ticker, weight in weights.items() if weight > 0}


def plan_moves(units: dict[str, float], prices: dict[str, float], weights: dict[str, float],
               cfg: Config, investable: float) -> dict[str, float]:
    """Limit both purchases and sales as a fraction of pocket value."""
    cur = {ticker: amount * prices[ticker] for ticker, amount in units.items()
           if ticker != CASH and amount > 0}
    want = {ticker: weight * investable for ticker, weight in weights.items()}
    names = set(cur) | set(want)
    out = sum(max(cur.get(t, 0.0) - want.get(t, 0.0), 0.0) for t in names)
    inn = sum(max(want.get(t, 0.0) - cur.get(t, 0.0), 0.0) for t in names)
    largest = max(out, inn)
    cap = cfg.step_pct / 100 * pocket_value(units, prices)
    alpha = 1.0 if not cur or largest == 0 else min(1.0, cap / largest)
    target = {t: cur.get(t, 0.0) + alpha * (want.get(t, 0.0) - cur.get(t, 0.0)) for t in names}
    return {t: value for t, value in target.items() if value > 1e-9}


def needs_moves(units: dict[str, float], prices: dict[str, float], weights: dict[str, float],
                cfg: Config) -> bool:
    """Check whether the planned changes exceed the configured band."""
    value = pocket_value(units, prices)
    if value <= 0:
        return False
    target = plan_moves(units, prices, weights, cfg, value)
    held = {t: u * prices[t] for t, u in units.items() if t != CASH and u > 0}
    names = set(target) | set(held)
    if cfg.band_pp == 0:
        return any(abs(target.get(t, 0.0) - held.get(t, 0.0)) / value > 1e-6 for t in names)
    return set(held) != set(target) or any(
        abs(target.get(t, 0.0) - held.get(t, 0.0)) / value > cfg.band_pp / 100 for t in names)


def rebalance_moves(units: dict[str, float], prices: dict[str, float], weights: dict[str, float],
                    market: str, cfg: Config) -> tuple[dict[str, float], float, float]:
    """Return new units, trading costs and traded notional for one pocket."""
    value = pocket_value(units, prices)
    current = {t: u * prices[t] for t, u in units.items() if t != CASH}
    cost = 0.0
    for _ in range(4):
        target = plan_moves(units, prices, weights, cfg, value - cost)
        deltas = {t: target.get(t, 0.0) - current.get(t, 0.0) for t in set(target) | set(current)}
        cost = sum(trade_cost(cfg, market, t, delta) for t, delta in deltas.items())
    new_units = {t: amount / prices[t] for t, amount in target.items()}
    new_units[CASH] = value - cost - sum(target.values())
    return new_units, cost, sum(abs(delta) for delta in deltas.values())


def trade_due(day: date, last_trade: date | None, every: str) -> bool:
    """Check whether the current trading period has had a successful trade."""
    if last_trade is None:
        return True
    if every == "day":
        return day != last_trade
    if every == "week":
        return day.isocalendar()[:2] != last_trade.isocalendar()[:2]
    if every == "month":
        return (day.year, day.month) != (last_trade.year, last_trade.month)
    raise ValueError(f"Unknown trading period: {every}")


def fx_gap(us_krw: float, kr_krw: float, cfg: Config) -> float:
    """Return the desired transfer into the US pocket in KRW."""
    total = us_krw + kr_krw
    if not cfg.fx_enabled or total <= 0 or abs(us_krw / total * 100 - cfg.us_pct) <= cfg.fx_band_pp:
        return 0.0
    return total * cfg.us_pct / 100 - us_krw


def fx_amount(gap_krw: float, source_krw: float, cfg: Config) -> float:
    """Cap the transfer by the source pocket's allowed movement."""
    return min(abs(gap_krw), cfg.step_pct / 100 * source_krw)
