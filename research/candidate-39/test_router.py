from __future__ import annotations

import math
from router import (
    BarObservation,
    FeatureObservation,
    RouteConfig,
    classify_symbol,
    route_universe,
)

MINUTE = 60_000_000_000


def _bar(i, o, h, l, c, v=100.0):
    return BarObservation(i * MINUTE, o, h, l, c, v)


def make_event(*, side=1, reclaim=False, distinct=True, lag=False):
    bars = []
    price = 100.0
    # 60-bar context: stable 2-point range, enough movement for a causal ATR.
    for i in range(60):
        drift = 0.015 * math.sin(i / 4)
        o = price
        c = 100.0 + drift + 0.12 * math.sin(i / 7)
        h = max(o, c) + 0.12
        l = min(o, c) - 0.12
        bars.append(_bar(i, o, h, l, c, 90 + i % 7))
        price = c
    context_high = max(x.high for x in bars)
    context_low = min(x.low for x in bars)
    boundary = context_high if side > 0 else context_low

    # 15-minute impulse ends outside the boundary.
    start = price
    target = boundary + side * (0.15 if not lag else 0.08)
    for j in range(15):
        i = 60 + j
        o = start + (target - start) * j / 15
        c = start + (target - start) * (j + 1) / 15
        h = max(o, c) + 0.10
        l = min(o, c) - 0.10
        bars.append(_bar(i, o, h, l, c, 150 + j))
    if reclaim:
        if side > 0:
            # Bar 1 freezes the failed upside interaction just inside the
            # context high.  Bars 2-3 then provide a distinct, later bearish
            # initiative without consuming the opposite-side objective.
            final_close = boundary - (0.08 if distinct else 0.025)
            resp = [
                (target, target + 0.10, boundary - 0.03, boundary - 0.01),
                (boundary - 0.01, boundary + 0.01, boundary - 0.06, boundary - 0.04),
                (boundary - 0.04, boundary - 0.03, boundary - 0.11, final_close),
            ]
        else:
            final_close = boundary + (0.08 if distinct else 0.025)
            resp = [
                (target, boundary + 0.03, target - 0.10, boundary + 0.01),
                (boundary + 0.01, boundary + 0.06, boundary - 0.01, boundary + 0.04),
                (boundary + 0.04, boundary + 0.11, boundary + 0.03, final_close),
            ]
    else:
        if side > 0:
            resp = [
                (target, target + 0.08, boundary + 0.01, target + 0.02),
                (target + 0.02, target + 0.09, boundary + 0.03, target + 0.025),
                (target + 0.025, target + 0.10, boundary + 0.04, target + 0.03),
            ]
        else:
            resp = [
                (target, boundary - 0.01, target - 0.08, target - 0.02),
                (target - 0.02, boundary - 0.03, target - 0.09, target - 0.025),
                (target - 0.025, boundary - 0.04, target - 0.10, target - 0.03),
            ]
    for j, values in enumerate(resp):
        bars.append(_bar(75 + j, *values, 175 + j))
    return bars


def config():
    return RouteConfig(
        min_route_score=2.8,
        min_participation_ratio=1.0,
        min_context_range_atr=0.5,
        min_geometry_r=0.7,
        min_oi_build=0.001,
        min_oi_contraction=0.001,
        min_flow_alignment=0.02,
        min_flow_flip=0.02,
    )


def test_build_accept_continuation_requires_oi_and_flow():
    bars = make_event(side=1)
    good = FeatureObservation(1, True, 0.08, 1.4, 0.10, 0.55, 0.008, 0.3)
    decision = classify_symbol(
        symbol="BTCUSDT", bars=bars, feature=good,
        peer_breadth=0.75, leader_side=1, leader_strength_atr=0.5,
        config=config(),
    )
    assert decision.state == "BUILD_ACCEPT_CONTINUATION"
    assert decision.side == 1
    assert decision.objective_reference > decision.entry_reference > decision.stop_reference

    oi_only = FeatureObservation(1, True, -0.08, 1.4, -0.10, 0.55, 0.008, 0.3)
    rejected = classify_symbol(
        symbol="BTCUSDT", bars=bars, feature=oi_only,
        peer_breadth=0.75, leader_side=1, leader_strength_atr=0.5,
        config=config(),
    )
    assert rejected.state == "UNRESOLVED"


def test_cascade_reclaim_requires_later_opposite_initiative():
    feature = FeatureObservation(1, True, 0.10, 1.6, -0.12, 0.35, -0.012, 0.4)
    good = classify_symbol(
        symbol="BTCUSDT", bars=make_event(side=1, reclaim=True, distinct=True),
        feature=feature, peer_breadth=0.5, leader_side=1,
        leader_strength_atr=0.4, config=config(),
    )
    assert good.state == "CASCADE_RECLAIM_REVERSAL"
    assert good.side == -1
    assert good.stop_reference > good.entry_reference > good.objective_reference
    assert "LATER_OPPOSITE_INITIATIVE" in good.reasons

    early = classify_symbol(
        symbol="BTCUSDT", bars=make_event(side=1, reclaim=True, distinct=False),
        feature=feature, peer_breadth=0.5, leader_side=1,
        leader_strength_atr=0.4, config=config(),
    )
    assert early.state == "UNRESOLVED"


def test_peer_led_repricing_is_separate_family():
    bars = make_event(side=1, lag=True)
    feature = FeatureObservation(1, True, 0.06, 1.35, 0.07, 0.5, 0.006, 0.1)
    decision = classify_symbol(
        symbol="ETHUSDT", bars=bars, feature=feature,
        peer_breadth=0.75, leader_side=1, leader_strength_atr=0.9,
        config=config(),
    )
    assert decision.state in {"PEER_LED_REPRICING", "BUILD_ACCEPT_CONTINUATION"}
    assert decision.side == 1


def test_universe_selects_one_same_event():
    bars = {symbol: make_event(side=1, lag=(symbol != "BTCUSDT")) for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")}
    features = {
        symbol: FeatureObservation(1, True, 0.08, 1.5, 0.09, 0.55, 0.007, 0.1)
        for symbol in bars
    }
    winner, decisions = route_universe(
        bars_by_symbol=bars, features_by_symbol=features, config=config()
    )
    assert winner is not None
    assert winner.symbol in bars
    assert sum(d.actionable for d in decisions.values()) >= 1
    # The router returns exactly one selected winner, never four independent trades.
    assert winner is max(
        (d for d in decisions.values() if d.actionable),
        key=lambda d: (d.score, d.expected_target_r, d.symbol == "BTCUSDT", d.symbol),
    )
