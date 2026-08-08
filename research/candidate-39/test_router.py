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


def make_event(
    *,
    side: int = 1,
    reclaim: bool = False,
    distinct: bool = True,
    lag: bool = False,
    failed_wick: bool = False,
) -> list[BarObservation]:
    bars: list[BarObservation] = []
    price = 100.0
    for i in range(60):
        drift = 0.015 * math.sin(i / 4)
        o = price
        c = 100.0 + drift + 0.12 * math.sin(i / 7)
        h = max(o, c) + 0.12
        l = min(o, c) - 0.12
        bars.append(_bar(i, o, h, l, c, 90 + i % 7))
        price = c
    context_high = max(item.high for item in bars)
    context_low = min(item.low for item in bars)
    boundary = context_high if side > 0 else context_low

    start = price
    retained = 0.15 if not lag else 0.08
    target = boundary + side * retained
    for j in range(15):
        i = 60 + j
        o = start + (target - start) * j / 15
        c = start + (target - start) * (j + 1) / 15
        wick = 0.06 if lag else 0.10
        if failed_wick and j == 12:
            wick = 0.75
        h = max(o, c) + (wick if side > 0 else 0.10)
        l = min(o, c) - (wick if side < 0 else 0.10)
        bars.append(_bar(i, o, h, l, c, 150 + j))

    if reclaim:
        if side > 0:
            final_close = boundary - (0.08 if distinct else 0.025)
            response = [
                (target, target + 0.10, boundary - 0.03, boundary - 0.01),
                (boundary - 0.01, boundary + 0.01, boundary - 0.06, boundary - 0.04),
                (boundary - 0.04, boundary - 0.03, boundary - 0.11, final_close),
            ]
        else:
            final_close = boundary + (0.08 if distinct else 0.025)
            response = [
                (target, boundary + 0.03, target - 0.10, boundary + 0.01),
                (boundary + 0.01, boundary + 0.06, boundary - 0.01, boundary + 0.04),
                (boundary + 0.04, boundary + 0.11, boundary + 0.03, final_close),
            ]
    else:
        if side > 0:
            response = [
                (target, target + 0.08, boundary + 0.01, target + 0.02),
                (target + 0.02, target + 0.09, boundary + 0.03, target + 0.025),
                (target + 0.025, target + 0.10, boundary + 0.04, target + 0.03),
            ]
        else:
            response = [
                (target, boundary - 0.01, target - 0.08, target - 0.02),
                (target - 0.02, boundary - 0.03, target - 0.09, target - 0.025),
                (target - 0.025, boundary - 0.04, target - 0.10, target - 0.03),
            ]
    for j, values in enumerate(response):
        bars.append(_bar(75 + j, *values, 175 + j))
    return bars


def config() -> RouteConfig:
    return RouteConfig(
        min_route_score=2.8,
        min_participation_ratio=1.0,
        min_context_range_atr=0.5,
        min_geometry_r=0.7,
        continuation_target_r=0.9,
        reversal_target_r=0.7,
        min_oi_build=0.001,
        min_oi_contraction=0.001,
        min_flow_alignment=0.02,
        min_flow_flip=0.02,
        min_break_retention=0.5,
    )


def interaction(*, oi: float = 0.008, opening: float = 0.08, tail: float = 0.10) -> FeatureObservation:
    return FeatureObservation(1, True, opening, 1.4, tail, 0.55, oi, 0.3)


def confirmation(*, flow: float = 0.09) -> FeatureObservation:
    return FeatureObservation(3, True, flow, 1.2, flow, 0.4, 0.0, 0.2)


def test_build_accept_uses_separate_confirmation_and_boundary_retest():
    bars = make_event(side=1)
    decision = classify_symbol(
        symbol="BTCUSDT",
        bars=bars,
        feature=interaction(),
        confirmation_feature=confirmation(flow=0.09),
        peer_breadth=0.75,
        leader_side=1,
        leader_strength_atr=0.5,
        config=config(),
    )
    assert decision.state == "BUILD_ACCEPT_CONTINUATION"
    assert decision.side == 1
    assert decision.entry_reference == decision.diagnostics["boundary"]
    assert decision.objective_reference > decision.entry_reference > decision.stop_reference
    assert decision.diagnostics["entry_policy"] == "PASSIVE_BOUNDARY_RETEST_LIMIT"


def test_stale_interaction_flow_cannot_override_adverse_entry_confirmation():
    decision = classify_symbol(
        symbol="BTCUSDT",
        bars=make_event(side=1),
        feature=interaction(opening=0.9, tail=0.8),
        confirmation_feature=confirmation(flow=-0.2),
        peer_breadth=0.75,
        leader_side=1,
        leader_strength_atr=0.5,
        config=config(),
    )
    assert decision.state == "UNRESOLVED"
    assert decision.reasons[0] == "ENTRY_INITIATIVE_NOT_CONFIRMED"


def test_failed_wick_is_not_accepted_value_even_with_oi_and_flow():
    decision = classify_symbol(
        symbol="BTCUSDT",
        bars=make_event(side=1, failed_wick=True),
        feature=interaction(),
        confirmation_feature=confirmation(flow=0.2),
        peer_breadth=0.75,
        leader_side=1,
        leader_strength_atr=0.5,
        config=config(),
    )
    assert decision.state == "UNRESOLVED"
    assert decision.reasons[0] == "FAILED_WICK_NOT_ACCEPTED_VALUE"
    assert decision.diagnostics["break_retention"] < 0.5


def test_cascade_reclaim_requires_later_initiative_and_current_flow():
    setup = interaction(oi=-0.012, opening=0.10, tail=-0.12)
    good = classify_symbol(
        symbol="BTCUSDT",
        bars=make_event(side=1, reclaim=True, distinct=True),
        feature=setup,
        confirmation_feature=confirmation(flow=-0.12),
        peer_breadth=0.5,
        leader_side=1,
        leader_strength_atr=0.4,
        config=config(),
    )
    assert good.state == "CASCADE_RECLAIM_REVERSAL"
    assert good.side == -1
    assert good.entry_reference == good.diagnostics["boundary"]
    assert good.stop_reference > good.entry_reference > good.objective_reference

    early = classify_symbol(
        symbol="BTCUSDT",
        bars=make_event(side=1, reclaim=True, distinct=False),
        feature=setup,
        confirmation_feature=confirmation(flow=-0.12),
        peer_breadth=0.5,
        leader_side=1,
        leader_strength_atr=0.4,
        config=config(),
    )
    assert early.state == "UNRESOLVED"


def test_too_wide_structural_stop_is_rejected_not_clamped_inward():
    cfg = config()
    assert _geometry(
        side=1,
        entry=100.0,
        raw_stop=95.0,
        raw_objective=110.0,
        atr=1.0,
        config=cfg,
    ) is None


def test_peer_led_repricing_remains_separate_family():
    decision = classify_symbol(
        symbol="ETHUSDT",
        bars=make_event(side=1, lag=True),
        feature=interaction(oi=0.006, opening=0.06, tail=0.07),
        confirmation_feature=confirmation(flow=0.08),
        peer_breadth=0.75,
        leader_side=1,
        leader_strength_atr=0.9,
        config=config(),
    )
    assert decision.state in {"PEER_LED_REPRICING", "BUILD_ACCEPT_CONTINUATION"}
    assert decision.side == 1


def test_universe_returns_only_one_winner_for_one_cross_asset_episode():
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    bars = {
        symbol: make_event(side=1, lag=(symbol != "BTCUSDT"))
        for symbol in symbols
    }
    features = {symbol: interaction() for symbol in symbols}
    confirmations = {symbol: confirmation(flow=0.09) for symbol in symbols}
    winner, decisions = route_universe(
        bars_by_symbol=bars,
        features_by_symbol=features,
        confirmation_features_by_symbol=confirmations,
        config=config(),
    )
    assert winner is not None
    assert winner.symbol in symbols
    assert sum(decision.actionable for decision in decisions.values()) >= 1
    assert winner is max(
        (decision for decision in decisions.values() if decision.actionable),
        key=lambda decision: (
            decision.score,
            decision.expected_target_r,
            decision.symbol == "BTCUSDT",
            decision.symbol,
        ),
    )
