from __future__ import annotations

import math

from router import BarObservation, FeatureObservation, RouteConfig, _cost_aware_geometry, route_universe

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


def _flat_series(start: float) -> list[BarObservation]:
    bars: list[BarObservation] = []
    minute = 60_000_000_000
    price = start
    for index in range(140):
        drift = 0.001 if index % 2 == 0 else -0.001
        close = price + drift
        bars.append(BarObservation((index + 1) * minute, price, max(price, close) + 0.02, min(price, close) - 0.02, close, 1000.0))
        price = close
    return bars


def test_cost_aware_target_exceeds_naive_raw_r_distance() -> None:
    cfg = RouteConfig(fee_rate_each_side=0.00075, slippage_rate_each_side=0.00025, funding_reserve_rate=0.00010, max_target_atr=100.0)
    long = _cost_aware_geometry(side=1, entry=100.0, raw_stop=99.0, atr=1.0, net_target_r=1.5, cfg=cfg)
    short = _cost_aware_geometry(side=-1, entry=100.0, raw_stop=101.0, atr=1.0, net_target_r=1.5, cfg=cfg)
    assert long is not None and short is not None
    long_stop, long_target, long_loss = long
    short_stop, short_target, short_loss = short
    assert long_stop == 99.0 and short_stop == 101.0
    assert long_target > 101.5
    assert short_target < 98.5
    assert long_loss > 1.0 and short_loss > 1.0
    assert math.isclose(long_loss, short_loss, rel_tol=0.0, abs_tol=0.01)


def test_unready_feature_cannot_trade() -> None:
    cfg = RouteConfig()
    bars = {symbol: _flat_series(100.0 + 20.0 * i) for i, symbol in enumerate(SYMBOLS)}
    features = {symbol: FeatureObservation(bars[symbol][-1].ts_event, ready=False) for symbol in SYMBOLS}
    winner, decisions = route_universe(bars_by_symbol=bars, features_by_symbol=features, config=cfg)
    assert winner is None
    assert all(not decision.actionable for decision in decisions.values())
