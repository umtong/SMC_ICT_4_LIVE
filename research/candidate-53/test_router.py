from __future__ import annotations

import math

from router import BarObservation, FeatureObservation, FLOW_RELEASE_CONTINUATION, RouteConfig, route_universe

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


def _series(start: float, side: int) -> list[BarObservation]:
    bars: list[BarObservation] = []
    price = start
    minute = 60_000_000_000
    for index in range(80):
        delta = side * (0.002 if index < 65 else 0.004)
        if index == 78:
            delta = side * 0.035
        open_ = price
        close = price + delta
        high = max(open_, close) + 0.010
        low = min(open_, close) - 0.010
        bars.append(BarObservation((index + 1) * minute, open_, high, low, close, 1_000.0))
        price = close
    return bars


def _feature(ts: int, side: int) -> FeatureObservation:
    return FeatureObservation(observed_time_ns=ts, ready=True, flow_open_10s=side * 0.20, notional_open_10s_burst=1.40, flow_60s=side * 0.30, efficiency_60s=0.20, oi_change_15m=0.01, premium_z=side * 0.10, flow_15s=side * 0.20, flow_3m=side * 0.30, notional_burst=1.20, trade_count_burst=1.10, absorption_60s=0.10, depth_imbalance_1=side * 0.20)


def test_continuation_is_direction_symmetric() -> None:
    cfg = RouteConfig(min_route_score=2.0, min_impulse_atr_continuation=0.30, min_participation_ratio=0.70, continuation_target_r=1.50, max_continuation_extension_atr=2.0, max_stop_atr=3.0)
    long_bars = {symbol: _series(100.0 + 20.0 * i, 1) for i, symbol in enumerate(SYMBOLS)}
    long_features = {symbol: _feature(bars[-1].ts_event, 1) for symbol, bars in long_bars.items()}
    long_winner, _ = route_universe(bars_by_symbol=long_bars, features_by_symbol=long_features, config=cfg)
    assert long_winner is not None and long_winner.state == FLOW_RELEASE_CONTINUATION and long_winner.side == 1
    short_bars = {symbol: _series(300.0 + 20.0 * i, -1) for i, symbol in enumerate(SYMBOLS)}
    short_features = {symbol: _feature(bars[-1].ts_event, -1) for symbol, bars in short_bars.items()}
    short_winner, _ = route_universe(bars_by_symbol=short_bars, features_by_symbol=short_features, config=cfg)
    assert short_winner is not None and short_winner.state == FLOW_RELEASE_CONTINUATION and short_winner.side == -1
    assert math.isclose(long_winner.score, short_winner.score, rel_tol=0.0, abs_tol=1e-8)


def test_unready_feature_cannot_trade() -> None:
    cfg = RouteConfig()
    bars = {symbol: _series(100.0 + 20.0 * i, 1) for i, symbol in enumerate(SYMBOLS)}
    features = {symbol: FeatureObservation(bars[symbol][-1].ts_event, ready=False) for symbol in SYMBOLS}
    winner, decisions = route_universe(bars_by_symbol=bars, features_by_symbol=features, config=cfg)
    assert winner is None
    assert all(not decision.actionable for decision in decisions.values())
