from __future__ import annotations

import math

from router import (
    BarObservation,
    FeatureObservation,
    RouteConfig,
    _geometry,
    classify_symbol,
    route_universe,
)

MINUTE = 60_000_000_000


def _bar(i: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> BarObservation:
    return BarObservation(i * MINUTE, o, h, l, c, v)


def cfg() -> RouteConfig:
    return RouteConfig(
        min_route_score=2.7,
        min_participation_ratio=1.0,
        min_context_range_atr=0.8,
        min_geometry_r=0.7,
        continuation_target_r=0.9,
        reversal_target_r=0.8,
        min_impulse_atr_continuation=0.55,
        min_impulse_atr_reversal=0.60,
        min_leader_excess_atr=0.10,
        min_peer_breadth_fraction=0.50,
        min_confirmation_flow=0.02,
        min_reversal_flow=0.02,
        min_impulse_path_efficiency=0.38,
        max_fresh_break_extension_atr=2.50,
        min_first_crack_atr=0.10,
        min_failure_resume_atr=0.04,
        min_mature_context_trend_atr=1.0,
        min_mature_context_range_atr=1.3,
        min_mature_close_location=0.58,
    )


def feature(*, oi: float = 0.004, flow: float = 0.08, burst: float = 1.4) -> FeatureObservation:
    return FeatureObservation(1, True, flow, burst, flow, 0.55, oi, 0.2)


def confirmation(*, flow: float = 0.08) -> FeatureObservation:
    return FeatureObservation(2, True, flow, 1.2, flow, 0.5, 0.0, 0.1)


def make_leader_event(*, side: int = 1, strength: float = 1.0, failed_turn: bool = False) -> list[BarObservation]:
    bars: list[BarObservation] = []
    price = 100.0
    for i in range(60):
        center = 100.0 + 0.16 * math.sin(i / 5.0)
        o = price
        c = center
        bars.append(_bar(i, o, max(o, c) + 0.11, min(o, c) - 0.11, c, 90 + i % 5))
        price = c
    high = max(item.high for item in bars)
    low = min(item.low for item in bars)
    boundary = high if side > 0 else low
    start = price
    finish = boundary + side * (0.34 * strength)
    for j in range(15):
        o = start + (finish - start) * j / 15.0
        c = start + (finish - start) * (j + 1) / 15.0
        bars.append(_bar(60 + j, o, max(o, c) + 0.035, min(o, c) - 0.035, c, 145 + j))

    extreme = max(bar.high for bar in bars[-15:]) if side > 0 else min(bar.low for bar in bars[-15:])
    if side > 0:
        response = [
            (finish, finish + 0.025, finish - 0.055, finish - 0.040),
            (finish - 0.040, finish - 0.020, finish - 0.085, finish - 0.065),
            (finish - 0.065, finish - 0.040, finish - 0.100, finish - 0.075),
            (finish - 0.075, finish - 0.025, finish - 0.085, finish - 0.030),
            (finish - 0.030, finish + 0.020, finish - 0.040, finish + 0.010),
            (
                finish + 0.010,
                finish + 0.080,
                finish - 0.005,
                finish - 0.020 if failed_turn else finish + 0.065,
            ),
        ]
    else:
        response = [
            (finish, finish + 0.055, finish - 0.025, finish + 0.040),
            (finish + 0.040, finish + 0.085, finish + 0.020, finish + 0.065),
            (finish + 0.065, finish + 0.100, finish + 0.040, finish + 0.075),
            (finish + 0.075, finish + 0.085, finish + 0.025, finish + 0.030),
            (finish + 0.030, finish + 0.040, finish - 0.020, finish - 0.010),
            (
                finish - 0.010,
                finish + 0.005,
                finish - 0.080,
                finish + 0.020 if failed_turn else finish - 0.065,
            ),
        ]
    for j, row in enumerate(response):
        bars.append(_bar(75 + j, *row, 130 - j))
    assert len(bars) == 81
    assert extreme == (max(bar.high for bar in bars[60:75]) if side > 0 else min(bar.low for bar in bars[60:75]))
    return bars


def make_mature_failure(*, side: int = 1, with_crack: bool = True, with_reentry: bool = True) -> list[BarObservation]:
    bars: list[BarObservation] = []
    price = 100.0
    for i in range(60):
        trend = side * 1.45 * i / 59.0
        wobble = 0.045 * math.sin(i / 3.0)
        c = 100.0 + trend + wobble
        o = price
        bars.append(_bar(i, o, max(o, c) + 0.11, min(o, c) - 0.11, c, 95 + i % 6))
        price = c
    boundary = max(bar.high for bar in bars) if side > 0 else min(bar.low for bar in bars)
    start = price
    probe = boundary + side * 0.18
    finish = boundary + side * 0.055
    for j in range(15):
        o = start + (finish - start) * j / 15.0
        c = start + (finish - start) * (j + 1) / 15.0
        extra = 0.14 if j in (10, 11) else 0.035
        h = max(o, c) + (extra if side > 0 else 0.035)
        l = min(o, c) - (extra if side < 0 else 0.035)
        bars.append(_bar(60 + j, o, h, l, c, 150 + j))
    last = bars[71]
    if side > 0:
        bars[71] = _bar(71, last.open, max(last.high, probe), last.low, last.close, last.volume)
    else:
        bars[71] = _bar(71, last.open, last.high, min(last.low, probe), last.close, last.volume)

    if side > 0:
        crack_close = boundary - (0.085 if with_crack else 0.005)
        reentry_high = boundary + 0.010 if with_reentry else boundary - 0.220
        response = [
            (finish, finish + 0.020, boundary - 0.020, boundary + 0.010),
            (boundary + 0.010, boundary + 0.015, boundary - 0.060, boundary - 0.045),
            (boundary - 0.045, boundary - 0.020, boundary - 0.105, crack_close),
            (crack_close, boundary - (0.020 if with_reentry else 0.180), crack_close - 0.010, boundary - (0.030 if with_reentry else 0.160)),
            (boundary - 0.030, reentry_high, boundary - 0.045, boundary - 0.020),
            (boundary - 0.020, boundary - 0.010, boundary - 0.120, boundary - 0.105),
        ]
    else:
        crack_close = boundary + (0.085 if with_crack else 0.005)
        reentry_low = boundary - 0.010 if with_reentry else boundary + 0.220
        response = [
            (finish, boundary + 0.020, finish - 0.020, boundary - 0.010),
            (boundary - 0.010, boundary + 0.060, boundary - 0.015, boundary + 0.045),
            (boundary + 0.045, boundary + 0.105, boundary + 0.020, crack_close),
            (crack_close, crack_close + 0.010, boundary + (0.020 if with_reentry else 0.180), boundary + (0.030 if with_reentry else 0.160)),
            (boundary + 0.030, boundary + 0.045, reentry_low, boundary + 0.020),
            (boundary + 0.020, boundary + 0.120, boundary + 0.010, boundary + 0.105),
        ]
    for j, row in enumerate(response):
        bars.append(_bar(75 + j, *row, 145 - j))
    return bars


def test_actual_leader_first_pullback_is_actionable():
    decision = classify_symbol(
        symbol="BTCUSDT",
        bars=make_leader_event(),
        feature=feature(),
        confirmation_feature=confirmation(flow=0.09),
        peer_breadth=0.75,
        leader_side=1,
        leader_strength_atr=1.25,
        leader_symbol="BTCUSDT",
        median_same_side_strength_atr=0.75,
        config=cfg(),
    )
    assert decision.state == "LEADER_FIRST_PULLBACK_CONTINUATION", decision
    assert decision.side == 1
    assert decision.objective_reference > decision.entry_reference > decision.stop_reference
    assert decision.diagnostics["entry_policy"] == "PASSIVE_RETEST_LIMIT"


def test_same_shape_on_laggard_is_rejected():
    decision = classify_symbol(
        symbol="ETHUSDT",
        bars=make_leader_event(strength=0.85),
        feature=feature(),
        confirmation_feature=confirmation(flow=0.09),
        peer_breadth=0.75,
        leader_side=1,
        leader_strength_atr=1.30,
        leader_symbol="BTCUSDT",
        median_same_side_strength_atr=0.75,
        config=cfg(),
    )
    assert decision.state == "UNRESOLVED"
    assert decision.reasons[0] == "LAGGARD_NOT_ACTUAL_LEADER"


def test_pullback_without_reacceleration_is_not_a_trade():
    decision = classify_symbol(
        symbol="BTCUSDT",
        bars=make_leader_event(failed_turn=True),
        feature=feature(),
        confirmation_feature=confirmation(flow=-0.08),
        peer_breadth=0.75,
        leader_side=1,
        leader_strength_atr=1.25,
        leader_symbol="BTCUSDT",
        median_same_side_strength_atr=0.75,
        config=cfg(),
    )
    assert decision.state == "UNRESOLVED"


def test_mature_extension_requires_crack_failed_reentry_and_resume():
    decision = classify_symbol(
        symbol="SOLUSDT",
        bars=make_mature_failure(),
        feature=feature(oi=-0.006, flow=0.08),
        confirmation_feature=confirmation(flow=-0.10),
        peer_breadth=0.50,
        leader_side=1,
        leader_strength_atr=0.9,
        leader_symbol="BTCUSDT",
        median_same_side_strength_atr=0.7,
        config=cfg(),
    )
    assert decision.state == "MATURE_EXTENSION_FAILED_REENTRY", decision
    assert decision.side == -1
    assert decision.stop_reference > decision.entry_reference > decision.objective_reference
    assert "REENTRY_ATTEMPT_FAILED" in decision.reasons


def test_immediate_wick_fade_without_first_crack_is_rejected():
    decision = classify_symbol(
        symbol="SOLUSDT",
        bars=make_mature_failure(with_crack=False),
        feature=feature(oi=-0.006, flow=0.08),
        confirmation_feature=confirmation(flow=-0.10),
        peer_breadth=0.50,
        leader_side=1,
        leader_strength_atr=0.9,
        leader_symbol="BTCUSDT",
        median_same_side_strength_atr=0.7,
        config=cfg(),
    )
    assert decision.state == "UNRESOLVED"
    assert decision.reasons[0] in {
        "MATURE_TREND_WITHOUT_FIRST_CRACK",
        "REENTRY_FAILURE_NOT_CONFIRMED",
        "CAUSAL_STATE_NOT_COHERENT",
    }


def test_crack_without_reentry_attempt_is_rejected():
    decision = classify_symbol(
        symbol="SOLUSDT",
        bars=make_mature_failure(with_reentry=False),
        feature=feature(oi=-0.006, flow=0.08),
        confirmation_feature=confirmation(flow=-0.10),
        peer_breadth=0.50,
        leader_side=1,
        leader_strength_atr=0.9,
        leader_symbol="BTCUSDT",
        median_same_side_strength_atr=0.7,
        config=cfg(),
    )
    assert decision.state == "UNRESOLVED"
    assert decision.reasons[0] == "NO_REENTRY_ATTEMPT_AFTER_CRACK"


def test_universe_selects_only_actual_leader_for_shared_expansion():
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    strengths = {"BTCUSDT": 1.25, "ETHUSDT": 0.75, "SOLUSDT": 0.65, "XRPUSDT": 0.60}
    bars = {symbol: make_leader_event(strength=value) for symbol, value in strengths.items()}
    features = {symbol: feature() for symbol in symbols}
    confirmations = {symbol: confirmation(flow=0.09) for symbol in symbols}
    winner, decisions = route_universe(
        bars_by_symbol=bars,
        features_by_symbol=features,
        confirmation_features_by_symbol=confirmations,
        config=cfg(),
    )
    assert winner is not None
    assert winner.symbol == "BTCUSDT"
    assert winner.state == "LEADER_FIRST_PULLBACK_CONTINUATION"
    assert sum(decision.actionable for decision in decisions.values()) == 1


def test_structural_stop_wider_than_policy_is_rejected_not_pulled_inward():
    assert _geometry(
        side=1,
        entry=100.0,
        raw_stop=95.0,
        raw_objective=108.0,
        atr=1.0,
        config=cfg(),
    ) is None


def test_causal_episode_id_is_stable_for_same_origin():
    base = classify_symbol(
        symbol="BTCUSDT",
        bars=make_leader_event(),
        feature=feature(),
        confirmation_feature=confirmation(flow=0.09),
        peer_breadth=0.75,
        leader_side=1,
        leader_strength_atr=1.25,
        leader_symbol="BTCUSDT",
        median_same_side_strength_atr=0.75,
        config=cfg(),
    )
    modified = make_leader_event()
    final = modified[-1]
    modified[-1] = _bar(80, final.open, final.high + 0.01, final.low, final.close + 0.005, final.volume)
    again = classify_symbol(
        symbol="BTCUSDT",
        bars=modified,
        feature=feature(),
        confirmation_feature=confirmation(flow=0.09),
        peer_breadth=0.75,
        leader_side=1,
        leader_strength_atr=1.25,
        leader_symbol="BTCUSDT",
        median_same_side_strength_atr=0.75,
        config=cfg(),
    )
    assert base.diagnostics["causal_episode_id"] == again.diagnostics["causal_episode_id"]
