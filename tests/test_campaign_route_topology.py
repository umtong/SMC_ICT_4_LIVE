from __future__ import annotations

from dataclasses import replace

import pytest

from smc_ict_4.campaign_policy.attack_ledger import (
    AttackOutcome, AttackRecord, CampaignPhase, CampaignSnapshot,
    OwnerSide, SourceKey, SourceSide,
)
from smc_ict_4.campaign_policy.liquidity_graph import SourceIdentity
from smc_ict_4.campaign_policy.route_topology import (
    CompletedRouteBar, RouteEntrySignal, RouteEventKind, RouteMode, RouteOpportunity,
    RoutePhase, SourceBand, SourceRouteTopology, ZoneKind,
)


HIGH_KEY = SourceKey("external-high", 1)
LOW_KEY = SourceKey("external-low", 1)
LOW_TARGET = SourceIdentity("external-low-objective", 1)
HIGH_TARGET = SourceIdentity("external-high-objective", 1)


def bar(t: int, open_: float, high: float, low: float, close: float) -> CompletedRouteBar:
    return CompletedRouteBar(t - 1, t, open_, high, low, close)


def source(side: SourceSide = SourceSide.HIGH) -> SourceBand:
    if side is SourceSide.HIGH:
        return SourceBand(HIGH_KEY, side, 100.0, 101.0, 0.1)
    return SourceBand(LOW_KEY, side, 99.0, 100.0, 0.1)


def route(side: SourceSide = SourceSide.HIGH, target: float | None = None) -> SourceRouteTopology:
    return SourceRouteTopology(
        source(side), attack_ordinal=1,
    )


def snapshot(
    owner: OwnerSide, *, side: SourceSide = SourceSide.HIGH, last_event: int = 10,
    response_completed: bool = False, frozen_control: float | None = None,
    response_end: int | None = None,
) -> CampaignSnapshot:
    key = HIGH_KEY if side is SourceSide.HIGH else LOW_KEY
    return CampaignSnapshot(
        key=key, campaign_id=f"BTCUSDT:{key.source_id}:1", start_time_ns=10,
        last_event_time_ns=last_event, phase=CampaignPhase.CLAIMED, owner=owner,
        attacks=(AttackRecord(
            ordinal=1, start_time_ns=10, end_time_ns=response_end,
            extreme=102.0 if side is SourceSide.HIGH else 98.0,
            intervening_response_extreme=(
                (99.0 if side is SourceSide.HIGH else 101.0) if response_completed else None
            ),
            frozen_control=frozen_control,
            outcome=AttackOutcome.RESPONSE_COMPLETED if response_completed else AttackOutcome.ACTIVE,
        ),),
        terminal_reason=None, terminal_time_ns=None,
    )


def opportunities(outputs: tuple[object, ...]) -> list[RouteEntrySignal]:
    return [item for item in outputs if isinstance(item, RouteEntrySignal)]


def event_kinds(outputs: tuple[object, ...]) -> list[RouteEventKind]:
    return [item.kind for item in outputs if hasattr(item, "kind")]


def feed_high_acceptance(topology: SourceRouteTopology, campaign: CampaignSnapshot, offset: int = 0) -> None:
    for row in (
        bar(10 + offset, 100.5, 102.0, 100.5, 101.5),
        bar(20 + offset, 101.5, 101.8, 101.2, 101.7),
        bar(30 + offset, 103.0, 103.2, 102.0, 102.0),
        bar(40 + offset, 102.3, 103.5, 102.2, 103.3),
    ):
        topology.on_bar(row, campaign=campaign, owner=OwnerSide.LONG)


def form_high_long_ob(topology: SourceRouteTopology, campaign: CampaignSnapshot, offset: int = 0) -> tuple[object, ...]:
    topology.on_bar(
        bar(50 + offset, 102.0, 102.2, 100.5, 101.5), campaign=campaign, owner=OwnerSide.LONG,
    )
    return topology.on_bar(
        bar(60 + offset, 101.4, 103.0, 100.6, 102.6), campaign=campaign, owner=OwnerSide.LONG,
    )


def feed_low_acceptance(topology: SourceRouteTopology, campaign: CampaignSnapshot) -> None:
    for row in (
        bar(10, 99.5, 99.5, 98.0, 98.5),
        bar(20, 98.5, 98.8, 98.2, 98.3),
        bar(30, 97.0, 98.0, 96.8, 98.0),
        bar(40, 97.7, 97.9, 96.2, 96.5),
    ):
        topology.on_bar(row, campaign=campaign, owner=OwnerSide.SHORT)


def form_low_short_ob(topology: SourceRouteTopology, campaign: CampaignSnapshot) -> tuple[object, ...]:
    topology.on_bar(
        bar(50, 97.5, 99.2, 97.3, 98.5), campaign=campaign, owner=OwnerSide.SHORT,
    )
    return topology.on_bar(
        bar(60, 98.6, 99.3, 96.2, 96.4), campaign=campaign, owner=OwnerSide.SHORT,
    )


def feed_high_rejection(topology: SourceRouteTopology) -> CampaignSnapshot:
    incomplete = snapshot(OwnerSide.SHORT)
    complete = snapshot(
        OwnerSide.SHORT, last_event=30, response_completed=True,
        frozen_control=99.5, response_end=30,
    )
    topology.on_bar(bar(10, 100.5, 102.0, 100.2, 101.5), campaign=incomplete, owner=OwnerSide.SHORT)
    topology.on_bar(bar(20, 100.2, 101.4, 99.5, 99.8), campaign=incomplete, owner=OwnerSide.SHORT)
    topology.on_bar(bar(30, 100.0, 100.2, 99.0, 99.4), campaign=complete, owner=OwnerSide.SHORT)
    topology.on_bar(bar(40, 99.6, 99.8, 98.8, 99.2), campaign=complete, owner=OwnerSide.SHORT)
    return complete


def test_outside_close_and_separation_alone_do_not_establish_acceptance() -> None:
    topology = route(target=120.0)
    campaign = snapshot(OwnerSide.LONG)
    outside = topology.on_bar(
        bar(10, 100.5, 102.0, 100.5, 101.5), campaign=campaign, owner=OwnerSide.LONG,
    )
    separated = topology.on_bar(
        bar(20, 101.4, 102.4, 101.2, 102.2), campaign=campaign, owner=OwnerSide.LONG,
    )
    continuation = topology.on_bar(
        bar(30, 102.2, 104.0, 102.0, 103.8), campaign=campaign, owner=OwnerSide.LONG,
    )
    assert event_kinds(outside) == [RouteEventKind.OUTSIDE_CLOSE_OBSERVED]
    assert event_kinds(separated) == [RouteEventKind.DISTINCT_SEPARATION_OBSERVED]
    assert not opportunities(continuation)
    assert topology.phase is RoutePhase.ACCEPTANCE_SEPARATED


def test_high_rejection_uses_post_confirmation_engulfing_ob_and_first_response_close() -> None:
    topology = route(target=90.0)
    campaign = feed_high_rejection(topology)
    no_zone = topology.on_bar(
        bar(50, 100.0, 100.7, 99.8, 100.5), campaign=campaign, owner=OwnerSide.SHORT,
    )
    frozen = topology.on_bar(
        bar(60, 100.6, 100.8, 99.2, 99.4), campaign=campaign, owner=OwnerSide.SHORT,
    )
    departed = topology.on_bar(
        bar(70, 100.1, 100.4, 99.5, 99.8), campaign=campaign, owner=OwnerSide.SHORT,
    )
    selected = topology.on_bar(
        bar(80, 100.4, 100.5, 99.2, 99.6), campaign=campaign, owner=OwnerSide.SHORT,
    )
    assert no_zone == ()
    assert event_kinds(frozen) == [RouteEventKind.CLAIM_ZONE_FROZEN]
    assert topology.entry_signal is not None
    plan = opportunities(selected)[0]
    assert plan.zone.kind is ZoneKind.ORDER_BLOCK
    assert plan.zone.formation_time_ns == (50, 60)
    assert plan.mode is RouteMode.FIRST_DEFENDED_RETURN
    assert plan.entry == pytest.approx(99.6)
    assert plan.stop == pytest.approx(102.1)
    assert event_kinds(departed) == [RouteEventKind.CLAIM_ZONE_DEPARTED]

    opportunity = topology.bind_target(
        plan,
        target_identity=LOW_TARGET,
        target=90.0,
    )
    assert isinstance(opportunity, RouteOpportunity)
    assert opportunity.target_identity == LOW_TARGET
    assert opportunity.gross_rr >= 1.0


def test_high_acceptance_has_no_direct_release_and_decides_on_first_retest_bar() -> None:
    topology = route(target=120.0)
    campaign = snapshot(OwnerSide.LONG)
    feed_high_acceptance(topology, campaign)
    assert topology.phase is RoutePhase.ACCEPTANCE_PROTECTED
    frozen = form_high_long_ob(topology, campaign)
    departed = topology.on_bar(
        bar(70, 102.0, 103.0, 101.8, 102.5), campaign=campaign, owner=OwnerSide.LONG,
    )
    selected = topology.on_bar(
        bar(80, 101.6, 102.8, 101.5, 102.4), campaign=campaign, owner=OwnerSide.LONG,
    )
    assert event_kinds(frozen) == [RouteEventKind.CLAIM_ZONE_FROZEN]
    assert event_kinds(departed) == [RouteEventKind.CLAIM_ZONE_DEPARTED]
    assert RouteEventKind.DIRECT_ROUTE_SELECTED not in event_kinds(frozen + departed + selected)
    assert event_kinds(selected) == [
        RouteEventKind.FIRST_RETURN_TOUCHED, RouteEventKind.FIRST_RETURN_ROUTE_SELECTED,
    ]
    plan = opportunities(selected)[0]
    assert plan.entry == pytest.approx(102.4)
    assert plan.decision == 80
    assert plan.zone.lower == pytest.approx(101.5)
    assert plan.zone.upper == pytest.approx(102.0)


def test_target_free_signal_emits_once_and_failed_first_binding_is_terminal() -> None:
    topology = route()
    campaign = snapshot(OwnerSide.LONG)
    feed_high_acceptance(topology, campaign)
    form_high_long_ob(topology, campaign)
    topology.on_bar(
        bar(70, 102.0, 103.0, 101.8, 102.5),
        campaign=campaign,
        owner=OwnerSide.LONG,
    )
    selected = topology.on_bar(
        bar(80, 101.6, 102.8, 101.5, 102.4),
        campaign=campaign,
        owner=OwnerSide.LONG,
    )
    signal = opportunities(selected)[0]
    assert not hasattr(signal, "target")
    assert topology.on_bar(
        bar(90, 102.4, 103.0, 102.0, 102.8),
        campaign=campaign,
        owner=OwnerSide.LONG,
    ) == ()

    # The first natural obstacle is bound before RR is known.  If it is under
    # 1R the route dies; a farther target cannot be tried afterward.
    assert topology.bind_target(
        signal,
        target_identity=HIGH_TARGET,
        target=103.0,
    ) is None
    assert topology.terminal
    with pytest.raises(Exception, match="exact unbound"):
        topology.bind_target(
            signal,
            target_identity=SourceIdentity("farther-high", 1),
            target=120.0,
        )


def test_low_acceptance_is_directionally_symmetric() -> None:
    topology = route(SourceSide.LOW, target=80.0)
    campaign = snapshot(OwnerSide.SHORT, side=SourceSide.LOW)
    feed_low_acceptance(topology, campaign)
    form_low_short_ob(topology, campaign)
    topology.on_bar(bar(70, 97.8, 98.0, 96.8, 97.0), campaign=campaign, owner=OwnerSide.SHORT)
    selected = topology.on_bar(
        bar(80, 98.0, 98.4, 96.8, 97.2), campaign=campaign, owner=OwnerSide.SHORT,
    )
    plan = opportunities(selected)[0]
    assert plan.owner_side is OwnerSide.SHORT
    assert plan.entry == pytest.approx(97.2)
    assert plan.stop == pytest.approx(100.1)


def test_low_rejection_is_directionally_symmetric() -> None:
    topology = route(SourceSide.LOW, target=110.0)
    incomplete = snapshot(OwnerSide.LONG, side=SourceSide.LOW)
    complete = snapshot(
        OwnerSide.LONG, side=SourceSide.LOW, last_event=30,
        response_completed=True, frozen_control=100.5, response_end=30,
    )
    topology.on_bar(bar(10, 99.5, 99.8, 98.0, 98.5), campaign=incomplete, owner=OwnerSide.LONG)
    topology.on_bar(bar(20, 99.0, 100.5, 98.8, 100.2), campaign=incomplete, owner=OwnerSide.LONG)
    topology.on_bar(bar(30, 100.0, 101.0, 99.8, 100.6), campaign=complete, owner=OwnerSide.LONG)
    topology.on_bar(bar(40, 100.3, 101.3, 100.1, 101.0), campaign=complete, owner=OwnerSide.LONG)
    topology.on_bar(bar(50, 99.5, 99.7, 98.8, 99.0), campaign=complete, owner=OwnerSide.LONG)
    topology.on_bar(bar(60, 98.9, 100.3, 98.7, 100.1), campaign=complete, owner=OwnerSide.LONG)
    topology.on_bar(bar(70, 99.4, 100.0, 99.2, 99.8), campaign=complete, owner=OwnerSide.LONG)
    selected = topology.on_bar(
        bar(80, 99.1, 100.5, 99.0, 99.8), campaign=complete, owner=OwnerSide.LONG,
    )
    plan = opportunities(selected)[0]
    assert plan.owner_side is OwnerSide.LONG
    assert plan.entry == pytest.approx(99.8)
    assert plan.stop == pytest.approx(97.9)


def test_event_local_fvg_requires_directional_middle_two_x_and_context_touch() -> None:
    topology = route(target=120.0)
    campaign = snapshot(OwnerSide.LONG)
    feed_high_acceptance(topology, campaign)
    topology.on_bar(bar(50, 100.5, 101.0, 100.0, 100.8), campaign=campaign, owner=OwnerSide.LONG)
    topology.on_bar(bar(60, 101.0, 103.2, 100.8, 103.0), campaign=campaign, owner=OwnerSide.LONG)
    frozen = topology.on_bar(
        bar(70, 102.0, 103.0, 101.5, 102.5), campaign=campaign, owner=OwnerSide.LONG,
    )
    topology.on_bar(bar(80, 101.6, 102.2, 101.4, 102.0), campaign=campaign, owner=OwnerSide.LONG)
    selected = topology.on_bar(
        bar(90, 101.2, 102.0, 101.1, 101.8), campaign=campaign, owner=OwnerSide.LONG,
    )
    assert event_kinds(frozen) == [RouteEventKind.CLAIM_ZONE_FROZEN]
    plan = opportunities(selected)[0]
    assert plan.zone.kind is ZoneKind.FVG
    assert plan.zone.strength_ratio is not None and plan.zone.strength_ratio >= 2.0
    assert plan.zone.formation_time_ns == (50, 60, 70)


def test_unengulfed_opposite_candle_is_not_an_order_block() -> None:
    topology = route(target=120.0)
    campaign = snapshot(OwnerSide.LONG)
    feed_high_acceptance(topology, campaign)
    topology.on_bar(bar(50, 102.0, 102.2, 100.5, 101.5), campaign=campaign, owner=OwnerSide.LONG)
    not_engulfed = topology.on_bar(
        bar(60, 101.6, 102.4, 101.4, 102.2), campaign=campaign, owner=OwnerSide.LONG,
    )
    assert not_engulfed == ()
    assert topology.phase is RoutePhase.ACCEPTANCE_PROTECTED


def test_first_touch_without_immediate_intended_response_is_terminal() -> None:
    topology = route(target=120.0)
    campaign = snapshot(OwnerSide.LONG)
    feed_high_acceptance(topology, campaign)
    form_high_long_ob(topology, campaign)
    topology.on_bar(bar(70, 102.0, 103.0, 101.8, 102.5), campaign=campaign, owner=OwnerSide.LONG)
    failed = topology.on_bar(
        bar(80, 102.2, 102.4, 101.5, 101.9), campaign=campaign, owner=OwnerSide.LONG,
    )
    later = topology.on_bar(
        bar(90, 101.8, 103.0, 101.7, 102.8), campaign=campaign, owner=OwnerSide.LONG,
    )
    assert event_kinds(failed) == [
        RouteEventKind.FIRST_RETURN_TOUCHED, RouteEventKind.FIRST_RETURN_FAILED,
    ]
    assert failed[-1].detail == "FIRST_RETURN_NOT_DEFENDED"
    assert later == ()


def test_full_stop_preserves_most_adverse_source_trigger_and_response_bound() -> None:
    topology = route(target=120.0)
    campaign = snapshot(OwnerSide.LONG)
    feed_high_acceptance(topology, campaign)
    form_high_long_ob(topology, campaign)
    topology.on_bar(bar(70, 102.0, 103.0, 101.8, 102.5), campaign=campaign, owner=OwnerSide.LONG)
    selected = topology.on_bar(
        bar(80, 101.6, 102.8, 100.2, 102.4), campaign=campaign, owner=OwnerSide.LONG,
    )
    plan = opportunities(selected)[0]
    # Source invalidation is 99.9, trigger invalidation is 100.4, and the
    # completed response wick contributes 100.1; the full stop takes the min.
    assert plan.zone.invalidation == pytest.approx(100.4)
    assert plan.stop == pytest.approx(99.9)


def test_no_ttl_and_lifecycle_terminals_are_preserved() -> None:
    topology = route(target=120.0)
    campaign = snapshot(OwnerSide.LONG)
    gap = 10**15
    feed_high_acceptance(topology, campaign, offset=gap)
    form_high_long_ob(topology, campaign, offset=gap)
    topology.on_bar(
        bar(gap + 70, 102.0, 103.0, 101.8, 102.5), campaign=campaign, owner=OwnerSide.LONG,
    )
    selected = topology.on_bar(
        bar(gap + 80, 101.6, 102.8, 101.5, 102.4), campaign=campaign, owner=OwnerSide.LONG,
    )
    assert opportunities(selected)[0].decision == gap + 80

    other = route(target=120.0)
    assert other.terminate(decision=10**18, reason="TARGET_CONSUMED")[0].detail == "TARGET_CONSUMED"


def test_reattack_owner_flip_and_future_state_are_preserved() -> None:
    first = snapshot(OwnerSide.LONG)
    topology = route(target=120.0)
    topology.on_bar(bar(10, 100.5, 102.0, 100.5, 101.5), campaign=first, owner=OwnerSide.LONG)
    second_attack = AttackRecord(2, 20, None, 103.0, None, None, AttackOutcome.ACTIVE)
    reattacked = topology.on_bar(
        bar(20, 101.5, 103.0, 101.2, 102.5),
        campaign=replace(first, last_event_time_ns=20, attacks=(*first.attacks, second_attack)),
        owner=OwnerSide.LONG,
    )
    assert reattacked[0].detail == "REATTACK_REPLACED_ROUTE:2"

    flipped_topology = route(target=120.0)
    flipped_topology.on_bar(
        bar(10, 100.5, 102.0, 100.5, 101.5), campaign=first, owner=OwnerSide.LONG,
    )
    flipped = flipped_topology.on_bar(
        bar(20, 101.5, 101.8, 100.5, 100.8),
        campaign=replace(first, owner=OwnerSide.SHORT, last_event_time_ns=20),
        owner=OwnerSide.SHORT,
    )
    assert flipped[0].detail == "OWNER_CHANGED"

    with pytest.raises(Exception, match="future state"):
        route(target=120.0).on_bar(
            bar(10, 100.5, 102.0, 100.5, 101.5),
            campaign=replace(first, last_event_time_ns=20), owner=OwnerSide.LONG,
        )
