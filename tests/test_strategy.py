from datetime import date

import pytest

from strategy import (CASH, Config, RankState, fx_amount, fx_gap, needs_moves,
                      plan_moves, pocket_weights, rebalance_moves, stock_weights,
                      trade_due, update_ranking)


def test_update_ranking_immediate_when_confirm_zero():
    ranked = ["B", "A", "C", "D", "E"]
    state = RankState(confirmed=["A", "B", "C", "D", "E"])
    assert update_ranking(state, ranked, Config(confirm_days=0)) == ranked


def test_update_ranking_waits_confirm_days():
    old = ["A", "B", "C", "D", "E"]
    new = ["B", "A", "C", "D", "E"]
    other = ["C", "B", "A", "D", "E"]
    state = RankState(confirmed=old.copy())
    cfg = Config(confirm_days=2)
    assert update_ranking(state, new, cfg) == old
    assert update_ranking(state, other, cfg) == old
    assert state.streak == 1
    assert update_ranking(state, new, cfg) == old
    assert update_ranking(state, new, cfg) == new
    assert state.pending == [] and state.streak == 0


def test_update_ranking_resets_on_universe_change():
    state = RankState(confirmed=["A", "B"], pending=["B", "A"], streak=1)
    assert update_ranking(state, ["A", "C"], Config(confirm_days=3)) == ["A", "C"]
    assert state.pending == [] and state.streak == 0


def test_stock_weights_by_rank():
    assert stock_weights(list("ABCDE"), Config()) == {"A": .5, "B": .3, "C": .2}


def test_stock_weights_renormalize_when_fewer_names():
    assert stock_weights(["A", "B"], Config()) == {"A": .625, "B": .375}
    assert stock_weights(["A", "B"], Config(rank_weights=(0, 0, 0, 50, 50))) == {}


def test_pocket_weights_split_bonds():
    stocks = {"A": .5, "B": .5}
    assert pocket_weights(stocks, "US", Config()) == pytest.approx({"A": .3, "B": .3, "SHY": .4})
    assert pocket_weights(stocks, "US", Config(short_bond_pct=25)) == pytest.approx(
        {"A": .3, "B": .3, "SHY": .1, "TLT": .3})
    assert pocket_weights({}, "US", Config(short_bond_pct=25)) == pytest.approx({"SHY": .25, "TLT": .75})


def test_plan_moves_caps_sells_and_buys():
    cfg = Config(step_pct=10)
    assert plan_moves({"X": 1000}, {"X": 1}, {"Y": 1}, cfg, 1000) == pytest.approx({"X": 900, "Y": 100})
    assert plan_moves({CASH: 500, "X": 500}, {"X": 1}, {"X": 1}, cfg, 1000) == pytest.approx({"X": 600})


def test_plan_moves_first_investment_uncapped():
    assert plan_moves({CASH: 1000}, {}, {"X": .6, "Y": .4}, Config(), 1000) == pytest.approx({"X": 600, "Y": 400})


def test_needs_moves_and_rebalance_moves():
    cfg = Config(step_pct=10, us_fee=0)
    units = {"X": 1000}
    prices = {"X": 1, "Y": 1}
    assert needs_moves(units, prices, {"Y": 1}, cfg)
    new, cost, traded = rebalance_moves(units, prices, {"Y": 1}, "US", cfg)
    assert new == pytest.approx({"X": 900, "Y": 100, CASH: 0})
    assert cost == 0 and traded == pytest.approx(200)
    assert not needs_moves({"X": 1000}, prices, {"X": 1}, cfg)
    assert not needs_moves({"X": 1000}, prices, {"X": .99}, Config(band_pp=2))
    assert needs_moves({"X": 1000}, prices, {"Y": 1}, Config(band_pp=50))


def test_trade_due_periods():
    assert not trade_due(date(2026, 11, 10), date(2026, 11, 9), "week")
    assert trade_due(date(2026, 11, 16), date(2026, 11, 9), "week")
    assert not trade_due(date(2026, 11, 30), date(2026, 11, 1), "month")
    assert trade_due(date(2026, 11, 10), date(2026, 11, 9), "day")
    assert trade_due(date(2026, 11, 10), None, "week")


def test_fx_gap_band_and_disabled():
    assert fx_gap(100, 0, Config(us_pct=60)) == -40.0
    assert fx_gap(64, 36, Config(us_pct=60, fx_band_pp=5)) == 0.0
    assert fx_gap(100, 0, Config(fx_enabled=False)) == 0.0
    assert fx_gap(0, 0, Config()) == 0.0


def test_fx_amount_capped_by_step():
    assert fx_amount(-40, 100, Config(step_pct=10)) == 10
