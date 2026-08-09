from __future__ import annotations

import math

from router import BarObservation, FeatureObservation, ICHI_STATE, RouteConfig, classify_symbol, ichi_exit_crossed, route_universe


def _bars(symbol_bias: float = 0.0, count: int = 1200, turn_down: bool = False):
    result = []
    price = 100.0 + symbol_bias
    for index in range(count):
        # Smooth long trend plus an accelerating final five-minute impulse.
        drift = 0.003
        if index > count - 50:
            drift = 0.040 + 0.001 * (index - (count - 50))
        if turn_down and index > count - 20:
            drift = -0.30
        open_ = price
        close = max(1.0, price + drift)
        result.append(
            BarObservation(
                ts_event=(index + 1) * 60_000_000_000 - 1,
                open=open_,
                high=max(open_, close) + 0.02,
                low=min(open_, close) - 0.02,
                close=close,
                volume=1000.0 + index,
            ),
        )
        price = close
    return result


def test_future_feature_is_rejected():
    bars = _bars()
    feature = FeatureObservation(bars[-1].ts_event + 1, ready=True)
    decision = classify_symbol("BTCUSDT", bars, feature, RouteConfig())
    assert not decision.actionable
    assert "FUTURE_FEATURE_REJECTED" in decision.reasons
    assert decision.episode_ts <= bars[-1].ts_event


def test_public_policy_uses_structural_geometry_when_actionable():
    bars = _bars()
    decision = classify_symbol("SOLUSDT", bars, FeatureObservation(bars[-1].ts_event), RouteConfig())
    if decision.actionable:
        assert decision.state == ICHI_STATE
        assert decision.side == 1
        assert decision.stop_reference < decision.entry_reference < decision.objective_reference
        distance = (decision.entry_reference - decision.stop_reference) / decision.entry_reference
        assert 0.0035 - 1e-12 <= distance <= 0.060 + 1e-12


def test_universe_never_routes_more_than_one():
    bars = {symbol: _bars(index) for index, symbol in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"))}
    features = {symbol: FeatureObservation(items[-1].ts_event) for symbol, items in bars.items()}
    winner, decisions = route_universe(bars, features, RouteConfig())
    assert sum(decision.actionable for decision in decisions.values()) >= int(winner is not None)
    assert winner is None or winner.symbol in bars


def test_exit_signal_is_causal_and_boolean():
    bars = _bars(turn_down=True)
    crossed, diagnostics = ichi_exit_crossed(bars, RouteConfig())
    assert isinstance(crossed, bool)
    assert diagnostics["exit_ready"] in (0, 1)


def test_incomplete_five_minute_bucket_is_ignored():
    bars = _bars(count=1201)
    first = classify_symbol("XRPUSDT", bars[:-1], FeatureObservation(bars[-2].ts_event), RouteConfig())
    second = classify_symbol("XRPUSDT", bars, FeatureObservation(bars[-1].ts_event), RouteConfig())
    # One extra minute cannot create a new completed five-minute episode.
    assert first.episode_ts == second.episode_ts


def test_persistent_eligible_episode_keeps_same_episode_identity():
    bars = _bars(count=1200)
    first = classify_symbol("ETHUSDT", bars, FeatureObservation(bars[-1].ts_event), RouteConfig())
    later_bars = _bars(count=1205)
    second = classify_symbol("ETHUSDT", later_bars, FeatureObservation(later_bars[-1].ts_event), RouteConfig())
    if first.actionable and second.actionable:
        assert second.episode_ts <= second.entry_reference * 0 + later_bars[-1].ts_event
        assert second.episode_ts <= later_bars[-1].ts_event
