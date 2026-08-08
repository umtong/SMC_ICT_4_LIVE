from __future__ import annotations

from dataclasses import replace

from test_router import MINUTE, _bar, config, make_event
from router_v3 import (
    FeatureObservation,
    TrappedBuildConfig,
    detect_trapped_build,
    evaluate_trapped_release,
    select_release_winner,
)


def _feature(
    ts: int,
    *,
    oi: float,
    flow: float,
    burst: float = 1.4,
) -> FeatureObservation:
    return FeatureObservation(
        observed_time_ns=ts,
        ready=True,
        flow_open_10s=flow,
        notional_open_10s_burst=burst,
        flow_60s=flow,
        efficiency_60s=0.55,
        oi_change_15m=oi,
        premium_z=0.2,
    )


def _setup():
    bars = make_event(side=1, failed_wick=True)
    setup = detect_trapped_build(
        symbol="BTCUSDT",
        bars=bars,
        pre_attack_feature=_feature(59 * MINUTE, oi=0.004, flow=0.01),
        attack_feature=_feature(74 * MINUTE, oi=0.008, flow=0.12),
        interaction_feature=_feature(75 * MINUTE, oi=0.006, flow=-0.01),
        confirmation_feature=_feature(77 * MINUTE, oi=0.005, flow=-0.02),
        route_config=config(),
        trap_config=TrappedBuildConfig(),
    )
    assert setup is not None
    return bars, setup


def _release_bars(bars, setup):
    boundary = setup.boundary
    bars = list(bars)
    bars.extend(
        [
            _bar(78, boundary - 0.01, boundary + 0.01, boundary - 0.07, boundary - 0.04, 190),
            _bar(79, boundary - 0.04, boundary - 0.01, boundary - 0.09, boundary - 0.06, 195),
            _bar(80, boundary - 0.06, boundary - 0.02, boundary - 0.25, boundary - 0.22, 240),
        ]
    )
    return bars


def test_trapped_build_is_setup_not_immediate_entry():
    bars, setup = _setup()
    assert setup.attack_side == 1
    assert setup.detected_ts == bars[-1].ts_event
    pending = evaluate_trapped_release(
        setup=setup,
        bars=bars,
        current_feature=_feature(77 * MINUTE, oi=0.005, flow=-0.2),
        route_config=config(),
    )
    assert pending.status == "PENDING"
    assert pending.decision is None


def test_separate_release_auction_produces_passive_new_leg_decision():
    bars, setup = _setup()
    released = evaluate_trapped_release(
        setup=setup,
        bars=_release_bars(bars, setup),
        current_feature=_feature(80 * MINUTE, oi=-0.004, flow=-0.14),
        route_config=config(),
    )
    assert released.status == "RELEASED"
    assert released.decision is not None
    assert released.decision.state == "TRAPPED_BUILD_RELEASE"
    assert released.decision.side == -1
    assert released.decision.stop_reference > released.decision.entry_reference
    assert released.decision.entry_reference > released.decision.objective_reference
    assert (
        released.decision.diagnostics["entry_policy"]
        == "PASSIVE_NEW_RELEASE_BOUNDARY_RETEST_LIMIT"
    )


def test_oi_contraction_during_later_release_is_allowed_not_relabelled_as_initial_cascade():
    bars, setup = _setup()
    released = evaluate_trapped_release(
        setup=setup,
        bars=_release_bars(bars, setup),
        current_feature=_feature(80 * MINUTE, oi=-0.02, flow=-0.14),
        route_config=config(),
    )
    assert released.status == "RELEASED"
    assert released.decision is not None
    assert released.decision.state == "TRAPPED_BUILD_RELEASE"


def test_attack_reacceptance_invalidates_pending_setup_before_release():
    bars, setup = _setup()
    resumed = list(bars)
    resumed.extend(
        [
            _bar(78, setup.boundary, setup.attack_extreme + 0.01, setup.boundary, setup.attack_extreme),
            _bar(79, setup.attack_extreme, setup.attack_extreme + 0.02, setup.boundary, setup.attack_extreme + 0.01),
            _bar(80, setup.attack_extreme, setup.attack_extreme + 0.03, setup.boundary, setup.attack_extreme + 0.02),
        ]
    )
    result = evaluate_trapped_release(
        setup=setup,
        bars=resumed,
        current_feature=_feature(80 * MINUTE, oi=0.005, flow=0.12),
        route_config=config(),
    )
    assert result.status == "INVALIDATED"
    assert result.reason == "ATTACK_REACCEPTED_OR_EXTREME_BREACHED"


def test_release_global_arbitration_rejects_near_tied_opposite_states():
    bars, setup = _setup()
    first = evaluate_trapped_release(
        setup=setup,
        bars=_release_bars(bars, setup),
        current_feature=_feature(80 * MINUTE, oi=-0.004, flow=-0.14),
        route_config=config(),
    ).decision
    assert first is not None
    opposite = replace(
        first,
        symbol="ETHUSDT",
        side=1,
        score=first.score - 0.05,
        entry_reference=100.0,
        stop_reference=99.0,
        objective_reference=103.0,
    )
    assert select_release_winner([first, opposite], ambiguity_score_gap=0.20) is None
