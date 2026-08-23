"""Synthesis contracts for reused research mechanisms.

Coverage maps to 4t residual ownership, structural-auction same-episode traps,
candidate-3b proof-route feasibility, and directional-v2 higher-timeframe context.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from smc_ict_4.episode_policy_live.domain import (
    Bar,
    EntryZone,
    LiquidityBoundary,
    TradePlan,
    stable_id,
)
from smc_ict_4.episode_policy_live.inventory_ownership import (
    InventoryInterpretation,
    InventoryRegime,
)
from smc_ict_4.episode_policy_live.policy import (
    EpisodeWatch,
    LiquidityEpisodeCoordinator,
    SymbolEpisodePolicy,
)
from smc_ict_4.episode_policy_live.structural_liquidity import (
    StructuralNode,
    StructureRole,
    TrendLineVersion,
    destination_first_geometry,
)

MIN = 60_000_000_000


def bar(
    minute: int,
    *,
    symbol: str = "BTCUSDT",
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    quote: float = 1_000_000.0,
    buy: float = 550_000.0,
    interval: int = 5,
) -> Bar:
    return Bar(
        symbol=symbol,
        interval_minutes=interval,
        open_time_ns=minute * MIN,
        close_time_ns=(minute + interval) * MIN - 1,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=quote / close,
        quote_volume=quote,
        taker_buy_quote_volume=buy,
        trade_count=100,
    )


def boundary(
    boundary_id: str,
    side: str,
    price: float,
    *,
    symbol: str = "BTCUSDT",
    observed: int = 0,
) -> LiquidityBoundary:
    return LiquidityBoundary(
        boundary_id=boundary_id,
        symbol=symbol,
        side=side,
        kind="SWING_60M",
        timeframe_minutes=60,
        observed_time_ns=observed,
        lower=price - 0.1,
        upper=price + 0.1,
        price=price,
        strength=3.0,
        anchor_serial=0,
    )


def add_objective(
    policy: SymbolEpisodePolicy,
    objective_id: str,
    side: str,
    price: float,
    *,
    observed: int,
    timeframe: int = 5,
    source_boundary_id: str = "",
) -> LiquidityBoundary:
    objective = LiquidityBoundary(
        boundary_id=objective_id,
        symbol=policy.symbol,
        side=side,
        kind=f"HORIZONTAL_OBJECTIVE_{timeframe}M",
        timeframe_minutes=timeframe,
        observed_time_ns=observed,
        lower=price,
        upper=price,
        price=price,
        strength=1.0,
    )
    policy.market.objective_book.register(
        objective,
        source_boundary_id=source_boundary_id,
    )
    return objective


def test_control_is_local_evidence_residualized_by_common_market() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    for index in range(12):
        policy.market.five_minute.append(bar(index * 5))
    impulse = bar(100, open_=100.0, high=102.2, low=99.8, close=102.0, buy=700_000.0)
    isolated = policy._bar_evidence(impulse, "LONG", 2.0, common_breadth=0.0)
    broad = policy._bar_evidence(impulse, "LONG", 2.0, common_breadth=1.0)
    assert isolated["local_control_score"] == broad["local_control_score"]
    assert broad["residual_control_score"] < isolated["residual_control_score"]
    assert broad["control_score"] == broad["residual_control_score"]


def test_common_component_excludes_the_symbol_being_evaluated() -> None:
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    policies = {symbol: SymbolEpisodePolicy(symbol, 0.1) for symbol in symbols}
    coordinator = LiquidityEpisodeCoordinator(policies)
    completed: dict[str, Bar] = {}
    for symbol in symbols:
        prior = bar(0, symbol=symbol, close=100.0)
        direction = 1.0 if symbol == "BTCUSDT" else -1.0
        current = bar(
            5,
            symbol=symbol,
            open_=100.0,
            high=102.0,
            low=98.0,
            close=100.0 + direction,
        )
        policies[symbol].market.five_minute[:] = [prior, current]
        completed[symbol] = current

    breadth = coordinator._peer_breadth(completed)

    assert breadth["BTCUSDT"] == -1.0
    assert abs(breadth["ETHUSDT"] - (-1.0 / 3.0)) < 1e-12


def test_higher_timeframe_direction_contextualizes_episode_ownership() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    policy.market.fifteen_minute[:] = [
        bar(0, close=100.0),
        bar(15, open_=100.0, high=103.0, low=99.5, close=102.5),
    ]
    policy.market.sixty_minute[:] = [
        bar(0, close=100.0),
        bar(60, open_=100.0, high=105.0, low=99.0, close=104.0),
    ]

    assert policy._higher_timeframe_context("LONG")["higher_timeframe_regime"] == "ALIGNED"
    assert policy._higher_timeframe_context("SHORT")["higher_timeframe_regime"] == "OPPOSED"


def test_accepted_failure_resolves_as_failed_path_of_same_interaction() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    source = boundary("SOURCE", "HIGH", 100.0)
    for minute in range(10):
        policy.journey.observe(
            bar(minute, interval=1, open_=99.8, high=100.0, low=99.6, close=99.8, buy=500_000.0)
        )
    interaction = bar(
        10, interval=1, open_=99.8, high=100.6, low=99.7, close=100.4, buy=700_000.0,
    )
    reclaim = bar(
        11, interval=1, open_=100.4, high=100.5, low=99.3, close=99.5, buy=200_000.0,
    )
    delivery = bar(
        12, interval=1, open_=99.5, high=99.7, low=99.0, close=99.2, buy=200_000.0,
    )
    for item in (interaction, reclaim, delivery):
        policy.journey.observe(item)
    policy._start_interaction(source, interaction, 0, "EXTERNAL_SWING")
    watch = next(iter(policy._watches.values()))
    journey = policy.journey.evaluate(policy._interaction(watch), delivery.close_time_ns)

    assert journey.completed
    assert journey.family == "FAILED_AUCTION_REVERSAL"
    assert watch.episode_id == stable_id(
        "BTCUSDT", source.boundary_id, interaction.open_time_ns, "AUCTION", prefix="EP:"
    )


def test_delivery_proof_does_not_cap_first_live_structural_target() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    decision = bar(100, open_=101.0, high=103.37, low=100.8, close=102.0)
    policy.market.five_minute.append(decision)
    policy.market.serial_5m = 20
    destination = add_objective(
        policy,
        "DEST",
        "HIGH",
        110.0,
        observed=decision.open_time_ns - MIN,
    )
    watch = EpisodeWatch(
        episode_id="EP:PROOF",
        family="FAILED_AUCTION_REVERSAL",
        source=boundary("SOURCE", "LOW", 99.0),
        side="LONG",
        state="RECLAIMED",
        interaction_serial=19,
        interaction_time_ns=decision.open_time_ns - 5 * MIN,
        event_extreme=98.0,
        last_update_serial=19,
        last_update_time_ns=decision.open_time_ns - 5 * MIN,
        bars_remaining=1,
        proof_extreme=103.37,
        ownership_balance=1.0,
    )
    zone = EntryZone("OB_FVG_SOURCE_CONFLUENCE", 99.0, 100.0, 0, decision.open_time_ns)

    plan = policy._build_plan(watch, decision, 20, 1.0, {"control_score": 1.0}, zone)

    assert plan is not None
    assert plan.target == 109.9
    assert plan.target > watch.proof_extreme
    assert plan.gross_rr >= 1.0
    assert plan.evidence["completion_target_origin"] == (
        "FIRST_LIVE_OPPOSING_HORIZONTAL_1M_SPAN6_OR_5M_15M_SPAN2_OBJECTIVE"
    )
    assert plan.evidence["delivery_proof_role"] == "SEQUENCE_COMPLETION_EVIDENCE_ONLY"


def test_failed_completion_bar_crossing_source_still_places_future_first_return() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    decision = bar(
        100, interval=1, open_=99.0, high=102.2, low=98.8, close=102.0,
    )
    policy.market.serial_5m = 20
    source = boundary("SOURCE:FAILED-RETURN", "LOW", 99.0)
    destination = add_objective(
        policy,
        "DEST:FAILED-RETURN",
        "HIGH",
        110.0,
        observed=decision.open_time_ns - MIN,
    )
    watch = EpisodeWatch(
        episode_id="EP:FAILED-RETURN",
        family="FAILED_AUCTION_REVERSAL",
        source=source,
        side="LONG",
        state="FAILED_AUCTION_RECLAIM_COMPLETED",
        interaction_serial=19,
        interaction_time_ns=decision.open_time_ns - MIN,
        event_extreme=98.5,
        last_update_serial=20,
        last_update_time_ns=decision.close_time_ns,
        ownership_balance=0.01,
    )
    zone = EntryZone(
        "SOURCE_ORDER_BLOCK", 99.5, 100.0, source.observed_time_ns,
        decision.open_time_ns,
    )

    plan = policy._build_plan(
        watch,
        decision,
        20,
        1.0,
        {"event_residual_ownership": 0.01},
        zone,
    )

    assert plan is not None
    assert plan.entry == 100.0
    assert decision.low <= plan.entry < decision.close
    assert policy._refresh_proposals([plan], decision) == [plan]
    later_return = bar(
        101, interval=1, open_=102.0, high=102.1, low=99.9, close=100.2,
    )
    assert policy._refresh_proposals([], later_return) == []
    assert policy.diagnostics["counts"]["FIRST_RETURN_ALREADY_PASSED"] == 1


def test_completed_episode_cannot_revive_farther_target_after_first_route_rejection() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    decision = bar(
        100, interval=1, open_=100.5, high=101.3, low=98.5, close=101.2,
    )
    policy.journey.observe(decision)
    source = boundary("SOURCE:IMMUTABLE-ROUTE", "LOW", 99.0)
    nearest = add_objective(
        policy,
        "DEST:NEAREST-SUB-R",
        "HIGH",
        101.5,
        observed=decision.open_time_ns - MIN,
    )
    farther = add_objective(
        policy,
        "DEST:FARTHER",
        "HIGH",
        110.0,
        observed=decision.open_time_ns - MIN,
    )
    watch = EpisodeWatch(
        episode_id="EP:IMMUTABLE-ROUTE",
        family="FAILED_AUCTION_REVERSAL",
        source=source,
        side="LONG",
        state="SOURCE_INTERACTION",
        interaction_serial=20,
        interaction_time_ns=decision.open_time_ns,
        event_extreme=98.5,
        last_update_serial=19,
        last_update_time_ns=decision.open_time_ns - 1,
        evidence={
            "interaction_source_lower": 98.9,
            "interaction_source_upper": 99.1,
        },
    )
    policy._watches[watch.episode_id] = watch
    completed = SimpleNamespace(
        completed=True,
        family="FAILED_AUCTION_REVERSAL",
        terminal_state="FAILED_AUCTION_RECLAIM_COMPLETED",
        completed_states=("FAILED_AUCTION_RECLAIM_COMPLETED",),
        phase_basis="STRUCTURAL_RANGE",
        control_transfer=True,
        activity_input_known=True,
        flow_input_known=True,
        response_time_ns=None,
        retest_time_ns=None,
        response_required_extreme=None,
        target_fresh=True,
        stop_intact=True,
    )
    peers = {
        symbol: [decision]
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    }
    ownership = {
        "event_residual_ownership": 0.01,
        "directional_posterior_support_rank": 1,
        "directional_family_transition_rank": 2,
    }

    with (
        patch.object(policy.journey, "evaluate", return_value=completed),
        patch.object(policy, "_event_ownership", return_value=ownership),
        patch.object(
            policy,
            "_origin_zone",
            return_value=EntryZone(
                "SOURCE_ORDER_BLOCK", 99.5, 100.0, 0, decision.open_time_ns,
            ),
        ),
    ):
        assert policy._advance_watches(decision, 20, 1.0, 0.0, peers) == []

    assert watch.episode_id not in policy._watches
    assert policy.diagnostics["counts"]["DESTINATION_BELOW_ONE_R"] == 1
    policy.market.objective_book.objectives.pop(nearest.boundary_id)
    policy.market.objective_book._active_ids.discard(nearest.boundary_id)
    later = bar(101, interval=1, open_=101.2, high=101.5, low=101.0, close=101.4)
    assert policy._advance_watches(later, 20, 1.0, 0.0, peers) == []


def test_history_truncation_terminalizes_watch_without_reviving_consumed_source() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    source = boundary("SOURCE:TRUNCATED", "HIGH", 100.0)
    policy.market.boundary_book.boundaries[source.boundary_id] = source
    interaction = bar(10, interval=1)
    policy._start_interaction(source, interaction, 2, "EXTERNAL_SWING")
    watch = next(iter(policy._watches.values()))
    assert source.boundary_id in policy._first_touch_time_by_structure
    unavailable = SimpleNamespace(
        completed=False,
        family=None,
        terminal_state="HISTORY_UNAVAILABLE",
        completed_states=(),
        phase_basis="STRUCTURAL_RANGE",
        control_transfer=False,
        activity_input_known=False,
        flow_input_known=False,
    )
    later = bar(20, interval=1)

    with patch.object(policy.journey, "evaluate", return_value=unavailable):
        assert policy._advance_watches(later, 4, 1.0, 0.0, None) == []

    assert watch.episode_id not in policy._watches
    assert source.boundary_id in policy._first_touch_time_by_structure
    assert policy.market.boundary_book.boundaries[source.boundary_id].consumed_time_ns == (
        interaction.close_time_ns
    )
    policy._create_boundary_watches(later, 4, 1.0, 0.0)
    assert not policy._watches


def test_conservative_tp_edge_cancels_plan_without_consuming_whole_pool() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    pool = boundary("DEST:POOL", "HIGH", 102.0)
    policy.market.boundary_book.boundaries[pool.boundary_id] = pool
    plan = replace(
        _plan("BTCUSDT", "EP:TP-EDGE", 10 * MIN),
        target=101.8,
        destination_boundary_id=pool.boundary_id,
    )
    policy._proposals[plan.episode_id] = plan
    touch = bar(
        12, interval=1, open_=101.5, high=101.8, low=101.4, close=101.7,
    )

    assert policy._refresh_proposals([], touch) == []
    assert policy.market.boundary_book.boundaries[pool.boundary_id].consumed_time_ns is None
    assert policy.market.boundary_book.boundaries[pool.boundary_id].is_fresh(
        touch.close_time_ns,
    )


def test_event_local_ob_refines_source_but_standalone_fvg_does_not_execute() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    bars = [bar(0, open_=100.0, high=101.0, low=98.8, close=99.0)]
    policy.market.five_minute[:] = bars
    policy.market.serial_5m = 4
    micro = [
        bar(10, interval=1, open_=100.3, high=100.4, low=99.9, close=100.0),
        bar(11, interval=1, open_=100.0, high=101.2, low=99.95, close=101.0),
        bar(12, interval=1, open_=101.0, high=101.4, low=100.6, close=101.2),
    ]
    policy.market.one_minute.extend(micro)
    source = LiquidityBoundary(
        boundary_id="SOURCE",
        symbol="BTCUSDT",
        side="LOW",
        kind="SWING_15M",
        timeframe_minutes=15,
        observed_time_ns=bars[0].close_time_ns,
        lower=100.15,
        upper=100.35,
        price=100.25,
        strength=2.0,
        anchor_serial=4,
    )
    watch = EpisodeWatch(
        episode_id="EP:LOCATION",
        family="FAILED_AUCTION_REVERSAL",
        source=source,
        side="LONG",
        state="RECLAIMED",
        interaction_serial=4,
        interaction_time_ns=micro[0].open_time_ns,
        event_extreme=99.5,
        last_update_serial=4,
        last_update_time_ns=micro[1].close_time_ns,
        bars_remaining=1,
    )

    zone = policy._origin_zone(watch, micro[-1], 1.0)

    assert zone.kind == "SOURCE_ORDER_BLOCK"
    assert zone.lower >= 100.15
    assert zone.upper <= 100.3
    assert policy.diagnostics["counts"]["STANDALONE_FVG_NOT_EXECUTABLE"] == 1


def test_accepted_journey_enters_first_response_close_not_second_return() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    for minute in range(10):
        policy.journey.observe(
            bar(minute, interval=1, open_=99.7, high=99.9, low=99.5, close=99.7, buy=500_000.0)
        )
    sequence = [
        bar(10, interval=1, open_=99.8, high=100.5, low=99.8, close=100.4, buy=700_000.0),
        bar(11, interval=1, open_=100.4, high=100.5, low=100.0, close=100.3, buy=650_000.0),
        bar(12, interval=1, open_=100.3, high=100.8, low=100.2, close=100.7, buy=700_000.0),
    ]
    for item in sequence:
        policy.journey.observe(item)
    source = boundary("SOURCE", "HIGH", 100.0)
    watch = EpisodeWatch(
        episode_id="EP:ACCEPTED",
        family="ACCEPTED_AUCTION_CONTINUATION",
        source=source,
        side="LONG",
        state="ACCEPTED_AUCTION_FIRST_RESPONSE_COMPLETED",
        interaction_serial=20,
        interaction_time_ns=sequence[0].open_time_ns,
        event_extreme=99.8,
        last_update_serial=20,
        last_update_time_ns=sequence[-2].close_time_ns,
        pullback_extreme=sequence[1].low,
        proof_extreme=sequence[-1].high,
        ownership_balance=0.01,
    )
    destination = add_objective(
        policy,
        "DEST",
        "HIGH",
        110.0,
        observed=sequence[0].open_time_ns - MIN,
    )
    policy.market.serial_5m = 20
    journey = policy.journey.evaluate(policy._interaction(watch), sequence[-1].close_time_ns)
    zone = EntryZone("TRANSFERRED_SOURCE", 99.9, 100.1, source.observed_time_ns, sequence[0].open_time_ns)

    plan = policy._build_plan(
        watch,
        sequence[-1],
        20,
        1.0,
        {"event_residual_ownership": 0.01},
        zone,
        journey,
    )

    assert plan is not None
    assert plan.entry == sequence[-1].close
    assert plan.evidence["entry_event"] == "ACCEPTANCE_FIRST_RESPONSE_CLOSE"


def test_accepted_journey_rejects_destination_spent_on_initial_breakout() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    for minute in range(10):
        policy.journey.observe(
            bar(minute, interval=1, open_=99.7, high=99.9, low=99.5, close=99.7)
        )
    sequence = [
        bar(10, interval=1, open_=99.8, high=102.2, low=99.8, close=100.4),
        bar(11, interval=1, open_=100.4, high=100.5, low=100.0, close=100.3),
        bar(12, interval=1, open_=100.3, high=100.8, low=100.2, close=100.7),
    ]
    for item in sequence:
        policy.journey.observe(item)
    source = boundary("SOURCE:SPENT", "HIGH", 100.0)
    watch = EpisodeWatch(
        episode_id="EP:ACCEPTED-SPENT",
        family="ACCEPTED_AUCTION_CONTINUATION",
        source=source,
        side="LONG",
        state="ACCEPTED_AUCTION_FIRST_RESPONSE_COMPLETED",
        interaction_serial=20,
        interaction_time_ns=sequence[0].open_time_ns,
        event_extreme=99.8,
        last_update_serial=20,
        last_update_time_ns=sequence[-1].close_time_ns,
        pullback_extreme=sequence[1].low,
        proof_extreme=sequence[-1].high,
        ownership_balance=0.01,
    )
    destination = add_objective(
        policy,
        "DEST:SPENT",
        "HIGH",
        102.0,
        observed=sequence[0].open_time_ns - MIN,
    )
    policy.market.serial_5m = 20
    journey = policy.journey.evaluate(policy._interaction(watch), sequence[-1].close_time_ns)
    zone = EntryZone(
        "TRANSFERRED_SOURCE", 99.9, 100.1, source.observed_time_ns,
        sequence[0].open_time_ns,
    )

    plan = policy._build_plan(
        watch,
        sequence[-1],
        20,
        1.0,
        {"event_residual_ownership": 0.01},
        zone,
        journey,
    )

    assert plan is None
    assert policy.diagnostics["counts"]["DESTINATION_SPENT_BEFORE_ENTRY"] == 1


def test_cross_market_roles_distinguish_common_and_independent_ownership() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    source = boundary("SOURCE", "LOW", 99.0)
    watch = EpisodeWatch(
        episode_id="EP:ROLE",
        family="FAILED_AUCTION_REVERSAL",
        source=source,
        side="LONG",
        state="COMPLETED",
        interaction_serial=1,
        interaction_time_ns=10 * MIN,
        event_extreme=98.0,
        last_update_serial=1,
        last_update_time_ns=10 * MIN,
    )
    decision = bar(12, interval=1, open_=100.8, high=102.2, low=100.7, close=102.0)
    paths = {
        "BTCUSDT": [
            bar(10, symbol="BTCUSDT", interval=1, open_=99.0, close=100.0),
            bar(11, symbol="BTCUSDT", interval=1, open_=100.0, close=101.0),
            decision,
        ],
        "ETHUSDT": [
            bar(10, symbol="ETHUSDT", interval=1, close=100.0),
            bar(11, symbol="ETHUSDT", interval=1, open_=100.0, close=100.4),
            bar(12, symbol="ETHUSDT", interval=1, open_=100.4, close=100.8),
        ],
        "SOLUSDT": [
            bar(10, symbol="SOLUSDT", interval=1, close=100.0),
            bar(11, symbol="SOLUSDT", interval=1, open_=100.0, close=100.3),
            bar(12, symbol="SOLUSDT", interval=1, open_=100.3, close=100.6),
        ],
        "XRPUSDT": [
            bar(10, symbol="XRPUSDT", interval=1, close=100.0),
            bar(11, symbol="XRPUSDT", interval=1, open_=100.0, close=100.2),
            bar(12, symbol="XRPUSDT", interval=1, open_=100.2, close=100.5),
        ],
    }
    # Official exact-edge tapes can contain the prior completed bar with a
    # close exactly equal to the interaction origin.  It belongs to the prior,
    # not as a duplicate event-path point beside the explicit origin.
    for symbol, values in paths.items():
        exact_edge = bar(9, symbol=symbol, interval=1, close=values[0].open)
        exact_values = exact_edge.to_dict()
        exact_values["close_time_ns"] = watch.interaction_time_ns
        values.insert(0, Bar(**exact_values))

    evidence = policy._event_ownership(watch, decision, paths)

    assert evidence["event_residual_ownership"] > 0.0
    assert evidence["cross_market_ownership_mode"] == "COMMON_CASCADE"
    assert evidence["cross_market_event_direction_rank"] == 1
    assert evidence["cross_market_sweep_time_ns"] == watch.interaction_time_ns
    assert abs(evidence["cross_market_signed_event_return"] - (102.0 - 99.0) / 99.0) < 1e-12


def test_missing_inventory_flow_and_composition_never_become_favorable_rank() -> None:
    class RaisingTimeline:
        def evaluate(self, **_values: object) -> object:
            raise AssertionError("unknown flow must not be coerced into zero")

    missing_flow = SymbolEpisodePolicy(
        "BTCUSDT", 0.1, inventory_timeline=RaisingTimeline(),  # type: ignore[arg-type]
    )
    unknown_bar = bar(
        10, interval=1, quote=0.0, buy=0.0, open_=100.0, close=101.0,
    )
    missing_flow.journey.observe(unknown_bar)
    watch = EpisodeWatch(
        episode_id="EP:INVENTORY-UNKNOWN",
        family="FAILED_AUCTION_REVERSAL",
        source=boundary("SOURCE:INVENTORY", "HIGH", 100.0),
        side="SHORT",
        state="COMPLETED",
        interaction_serial=1,
        interaction_time_ns=unknown_bar.open_time_ns,
        event_extreme=101.0,
        last_update_serial=1,
        last_update_time_ns=unknown_bar.close_time_ns,
    )

    evidence = missing_flow._inventory_evidence(watch, unknown_bar)

    assert evidence["inventory_reason"] == "PRICE_OR_FLOW_MISSING"
    assert evidence["inventory_coherence_rank"] == 0

    class UnknownCompositionTimeline:
        def evaluate(self, **values: object) -> object:
            return SimpleNamespace(
                known=False,
                regime=InventoryRegime.POSITION_RESET,
                interpretation=InventoryInterpretation.UNKNOWN,
                reason="POSITION_RESET_WITH_UNKNOWN_ACCOUNT_COMPOSITION",
                oi_change_fraction=-0.1,
            )

    missing_composition = SymbolEpisodePolicy(
        "BTCUSDT",
        0.1,
        inventory_timeline=UnknownCompositionTimeline(),  # type: ignore[arg-type]
    )
    known_flow_bar = bar(10, interval=1, open_=100.0, close=99.0)
    missing_composition.journey.observe(known_flow_bar)
    evidence = missing_composition._inventory_evidence(watch, known_flow_bar)

    assert evidence["inventory_interpretation"] == "UNKNOWN"
    assert evidence["inventory_coherence_rank"] == 0


def test_directional_category_uses_posterior_sign_not_delta_alone() -> None:
    accepted = EpisodeWatch(
        episode_id="EP:DIRECTION:ACCEPTED",
        family="ACCEPTED_AUCTION_CONTINUATION",
        source=boundary("SOURCE:DIRECTION:A", "HIGH", 100.0),
        side="LONG",
        state="COMPLETED",
        interaction_serial=1,
        interaction_time_ns=MIN,
        event_extreme=99.0,
        last_update_serial=2,
        last_update_time_ns=2 * MIN,
    )
    carried_but_eased = SimpleNamespace(
        prior=SimpleNamespace(trend_alignment=0.9),
        posterior=SimpleNamespace(trend_alignment=0.8),
        trend_alignment_update=-0.1,
    )

    accepted_evidence = SymbolEpisodePolicy._directional_ownership_category(
        accepted, carried_but_eased,
    )

    assert accepted_evidence["directional_posterior_support_state"] == "SUPPORTED"
    assert accepted_evidence["directional_family_transition_state"] == (
        "CARRIED_PRIOR_ALIGNED_CONTINUATION"
    )

    failed = replace(
        accepted,
        episode_id="EP:DIRECTION:FAILED",
        family="FAILED_AUCTION_REVERSAL",
    )
    less_opposed_but_still_opposed = SimpleNamespace(
        prior=SimpleNamespace(trend_alignment=-0.9),
        posterior=SimpleNamespace(trend_alignment=-0.8),
        trend_alignment_update=0.1,
    )
    failed_evidence = SymbolEpisodePolicy._directional_ownership_category(
        failed, less_opposed_but_still_opposed,
    )

    assert failed_evidence["directional_posterior_support_state"] == "OPPOSED"
    assert failed_evidence["directional_family_transition_state"] == (
        "POST_SWEEP_DIRECTIONAL_TRANSFER_ABSENT"
    )


def test_htf_line_projection_uses_local_serial_then_global_five_minute_clock() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    policy.market.five_minute[:] = [bar(index * 5) for index in range(51)]
    policy.market.serial_5m = 50
    cases = ((15, 3.0, "LINE:15M"), (60, 12.0, "LINE:60M"))
    for timeframe, local_slope, line_id in cases:
        book = policy._trend_channel_books[timeframe]
        for local_serial in range(3):
            book.observe_bar(bar(local_serial * timeframe, interval=timeframe))
        book.lines.append(
            TrendLineVersion(
                line_id=line_id,
                symbol="BTCUSDT",
                side="HIGH",
                timeframe_minutes=timeframe,
                first_pivot_id=f"P:{timeframe}:0",
                second_pivot_id=f"P:{timeframe}:1",
                first_serial=0,
                second_serial=1,
                first_price=100.0,
                second_price=100.0 + local_slope,
                observed_time_ns=book.bars[1].close_time_ns,
                version=1,
            )
        )

    decision_time_ns = 181 * MIN
    global_serial = 50
    current = {
        node.node_id: node
        for node in policy._projected_structural_nodes(decision_time_ns, global_serial)
    }
    following = {
        node.node_id: node
        for node in policy._projected_structural_nodes(decision_time_ns, global_serial + 1)
    }

    for timeframe, local_slope, line_id in cases:
        latest_local_price = 100.0 + 2.0 * local_slope
        final_five_open = (
            policy._trend_channel_books[timeframe].bars[-1].open_time_ns
            + (timeframe - 5) * MIN
        )
        latest_global_serial = next(
            index
            for index, item in enumerate(policy.market.five_minute)
            if item.open_time_ns == final_five_open
        )
        expected = latest_local_price + (local_slope * 5.0 / timeframe) * (
            global_serial - latest_global_serial
        )
        current_mid = sum(current[line_id].band_at(global_serial)) / 2.0
        following_mid = sum(following[line_id].band_at(global_serial + 1)) / 2.0
        assert abs(current_mid - expected) < 1e-12
        assert abs(following_mid - current_mid - 1.0) < 1e-12
        assert current[line_id].slope_per_bar == 1.0

    route_owner = EpisodeWatch(
        episode_id="EP:ROUTE-CLOCK",
        family="FAILED_AUCTION_REVERSAL",
        source=boundary("SOURCE:ROUTE-CLOCK", "LOW", 90.0),
        side="LONG",
        state="COMPLETED",
        interaction_serial=0,
        interaction_time_ns=10 * MIN,
        event_extreme=89.0,
        last_update_serial=global_serial,
        last_update_time_ns=decision_time_ns,
    )
    route = {
        node.node_id: node
        for node in policy._route_nodes(route_owner, decision_time_ns, global_serial)
    }
    assert route["LINE:15M"].slope_per_bar == 1.0
    assert route["LINE:60M"].slope_per_bar == 1.0
    route_current = sum(route["LINE:15M"].band_at(global_serial)) / 2.0
    route_following = sum(route["LINE:15M"].band_at(global_serial + 1)) / 2.0
    assert abs(route_following - route_current - 1.0) < 1e-12


def test_htf_projection_uses_replay_local_anchor_at_2024_epoch_offset() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    epoch_2024 = 1_704_067_200_000_000_000
    replay_start = epoch_2024 - 100 * MIN

    def shifted(item: Bar, offset: int, *, exact_close: bool = False) -> Bar:
        values = item.to_dict()
        values["open_time_ns"] = int(values["open_time_ns"]) + offset
        values["close_time_ns"] = (
            int(values["close_time_ns"]) + offset + (1 if exact_close else 0)
        )
        return Bar(**values)

    policy.market.five_minute[:] = [
        shifted(bar(index * 5), replay_start) for index in range(50)
    ]
    policy.market.serial_5m = 49
    book = policy._trend_channel_books[15]
    for local_serial in range(3):
        book.observe_bar(
            shifted(
                bar(local_serial * 15, interval=15),
                epoch_2024,
                exact_close=True,
            )
        )
    book.lines.append(
        TrendLineVersion(
            line_id="LINE:EPOCH-2024",
            symbol="BTCUSDT",
            side="HIGH",
            timeframe_minutes=15,
            first_pivot_id="P:EPOCH:0",
            second_pivot_id="P:EPOCH:1",
            first_serial=0,
            second_serial=1,
            first_price=100.0,
            second_price=103.0,
            observed_time_ns=book.bars[1].close_time_ns,
            version=1,
        )
    )

    node = next(
        item
        for item in policy._projected_structural_nodes(
            policy.market.five_minute[-1].close_time_ns, 49,
        )
        if item.node_id == "LINE:EPOCH-2024"
    )

    # Latest 15m local value is 106 at replay-local 5m serial 28; the
    # current serial is 49, so +21 five-minute slope units produces 127.
    assert abs(sum(node.band_at(49)) / 2.0 - 127.0) < 1e-12


def test_htf_projection_fails_closed_when_constituent_five_minute_bar_is_missing() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    book = policy._trend_channel_books[15]
    book.observe_bar(bar(0, interval=15))
    book.observe_bar(bar(15, interval=15))
    book.lines.append(
        TrendLineVersion(
            line_id="LINE:BROKEN-CLOCK",
            symbol="BTCUSDT",
            side="HIGH",
            timeframe_minutes=15,
            first_pivot_id="P:BROKEN:0",
            second_pivot_id="P:BROKEN:1",
            first_serial=0,
            second_serial=1,
            first_price=100.0,
            second_price=103.0,
            observed_time_ns=book.bars[0].close_time_ns,
            version=1,
        )
    )

    assert policy._projected_structural_nodes(31 * MIN, 50) == []
    assert policy.diagnostics["counts"]["STRUCTURAL_CLOCK_ANCHOR_UNAVAILABLE"] == 1


def test_first_touch_owns_static_and_projected_source_within_same_five_minute_bar() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    static_source = boundary("SOURCE:STATIC-ONCE", "HIGH", 100.0)
    policy.market.boundary_book.boundaries[static_source.boundary_id] = static_source
    first_static = bar(
        10, interval=1, open_=100.0, high=100.5, low=99.9, close=100.4,
    )
    second_static = bar(
        11, interval=1, open_=100.1, high=100.6, low=99.9, close=100.5,
    )

    policy._create_boundary_watches(first_static, 2, 1.0, 0.0)
    static_episodes = list(policy._watches)
    assert len(static_episodes) == 1
    policy._watches.pop(static_episodes[0])  # simulate a terminal no-trade journey
    policy._create_boundary_watches(second_static, 2, 1.0, 0.0)

    assert not policy._watches
    assert policy._first_touch_time_by_structure[static_source.boundary_id] == (
        first_static.close_time_ns
    )
    assert policy.market.boundary_book.boundaries[
        static_source.boundary_id
    ].consumed_time_ns == first_static.close_time_ns

    book = policy._trend_channel_books[15]
    policy.market.five_minute[:] = [bar(index * 5) for index in range(6)]
    policy.market.serial_5m = 5
    book.observe_bar(bar(0, interval=15))
    book.observe_bar(bar(15, interval=15))
    projected_id = "LINE:PROJECTED-ONCE"
    book.lines.append(
        TrendLineVersion(
            line_id=projected_id,
            symbol="BTCUSDT",
            side="HIGH",
            timeframe_minutes=15,
            first_pivot_id="P:PROJECTED:0",
            second_pivot_id="P:PROJECTED:1",
            first_serial=0,
            second_serial=1,
            first_price=100.0,
            second_price=100.0,
            observed_time_ns=book.bars[0].close_time_ns,
            version=1,
        )
    )
    first_projected = bar(
        31, interval=1, open_=100.0, high=100.5, low=99.9, close=100.4,
    )
    second_projected = bar(
        32, interval=1, open_=100.1, high=100.6, low=99.9, close=100.5,
    )

    policy._create_boundary_watches(first_projected, 5, 1.0, 0.0)
    projected_episodes = list(policy._watches)
    assert len(projected_episodes) == 1
    policy._watches.pop(projected_episodes[0])
    policy._create_boundary_watches(second_projected, 5, 1.0, 0.0)

    assert not policy._watches
    assert policy._first_touch_time_by_structure[projected_id] == (
        first_projected.close_time_ns
    )
    assert "source_interaction_claims" not in policy.export_state()


def test_overlapping_source_owner_is_insertion_order_invariant() -> None:
    source_15 = boundary("SOURCE:OVERLAP:15", "HIGH", 100.0)
    source_15 = replace(source_15, timeframe_minutes=15, kind="SWING_15M")
    source_60 = boundary("SOURCE:OVERLAP:60", "HIGH", 100.05)
    interaction = bar(
        10, interval=1, open_=100.0, high=100.6, low=99.8, close=100.5,
    )

    owners: list[tuple[str, float, float]] = []
    for ordered in ((source_15, source_60), (source_60, source_15)):
        policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
        policy.market.boundary_book.boundaries = {
            item.boundary_id: item for item in ordered
        }
        policy._create_boundary_watches(interaction, 2, 1.0, 0.0)
        assert len(policy._watches) == 1
        watch = next(iter(policy._watches.values()))
        owners.append(
            (
                watch.source.boundary_id,
                float(watch.evidence["interaction_source_lower"]),
                float(watch.evidence["interaction_source_upper"]),
            )
        )
        assert set(policy._first_touch_time_by_structure) == {
            source_15.boundary_id,
            source_60.boundary_id,
        }

    assert owners == [owners[0], owners[0]]
    assert owners[0][0] == source_60.boundary_id


def test_bridge_overlap_forms_one_transitive_source_component() -> None:
    def level(boundary_id: str, lower: float, upper: float) -> LiquidityBoundary:
        return replace(
            boundary(boundary_id, "HIGH", (lower + upper) / 2.0),
            lower=lower,
            upper=upper,
            price=(lower + upper) / 2.0,
        )

    a = replace(level("SOURCE:A", 99.90, 100.10), strength=3.0)
    b = replace(level("SOURCE:B", 100.05, 100.25), strength=2.0)
    c = replace(level("SOURCE:C", 100.20, 100.40), strength=1.0)
    interaction = bar(
        10, interval=1, open_=100.0, high=100.5, low=99.8, close=100.3,
    )

    owners: list[str] = []
    for ordered in ((a, b, c), (c, b, a)):
        policy = SymbolEpisodePolicy("BTCUSDT", 0.01)
        policy.market.boundary_book.boundaries = {
            item.boundary_id: item for item in ordered
        }
        policy._create_boundary_watches(interaction, 2, 1.0, 0.0)
        assert len(policy._watches) == 1
        owners.append(next(iter(policy._watches.values())).source.boundary_id)
        assert set(policy._first_touch_time_by_structure) == {
            a.boundary_id,
            b.boundary_id,
            c.boundary_id,
        }

    assert owners == [a.boundary_id, a.boundary_id]


def test_confirmation_candle_cannot_retroactively_touch_new_source_or_node() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    confirmation = bar(
        30, interval=1, open_=100.0, high=100.5, low=99.8, close=100.2,
    )
    observed_during_bar = confirmation.open_time_ns + 1
    static_source = boundary(
        "SOURCE:JUST-CONFIRMED", "HIGH", 100.0, observed=observed_during_bar,
    )
    policy.market.boundary_book.boundaries[static_source.boundary_id] = static_source
    book = policy._trend_channel_books[15]
    policy.market.five_minute[:] = [bar(index * 5) for index in range(6)]
    policy.market.serial_5m = 5
    book.observe_bar(bar(0, interval=15))
    book.observe_bar(bar(15, interval=15))
    projected_id = "LINE:JUST-CONFIRMED"
    book.lines.append(
        TrendLineVersion(
            line_id=projected_id,
            symbol="BTCUSDT",
            side="HIGH",
            timeframe_minutes=15,
            first_pivot_id="P:CONFIRM:0",
            second_pivot_id="P:CONFIRM:1",
            first_serial=0,
            second_serial=1,
            first_price=100.0,
            second_price=100.0,
            observed_time_ns=observed_during_bar,
            version=1,
        )
    )

    policy._create_boundary_watches(confirmation, 5, 1.0, 0.0)

    assert not policy._watches
    assert not policy._first_touch_time_by_structure
    assert policy.diagnostics["counts"][
        "SOURCE_NOT_OBSERVABLE_AT_INTERACTION_OPEN"
    ] == 2

    later_touch = bar(
        31, interval=1, open_=100.2, high=100.5, low=99.9, close=100.4,
    )
    policy._create_boundary_watches(later_touch, 5, 1.0, 0.0)

    assert len(policy._watches) == 1
    assert set(policy._first_touch_time_by_structure) == {
        static_source.boundary_id,
        projected_id,
    }


def _plan(symbol: str, episode: str, interaction: int) -> TradePlan:
    return TradePlan(
        episode_id=episode,
        plan_id=f"PLAN:{episode}",
        symbol=symbol,
        family="FAILED_AUCTION_REVERSAL",
        side="LONG",
        decision_time_ns=interaction + MIN,
        entry=100.0,
        stop=99.0,
        target=101.2,
        expires_time_ns=interaction + 60 * MIN,
        source_boundary_id="SOURCE",
        destination_boundary_id="DEST",
        entry_zone=EntryZone("SOURCE_LOCATION", 99.8, 100.0, 0, 0),
        evidence={"interaction_time_ns": interaction},
    )


def test_directional_semantics_precede_magnitude_without_becoming_hard_gate() -> None:
    opposed = replace(
        _plan("BTCUSDT", "EP:OPPOSED", 10 * MIN),
        evidence={
            "interaction_time_ns": 10 * MIN,
            "directional_posterior_support_rank": 0,
            "directional_family_transition_rank": 0,
            "counterfactual_ownership_per_risk": 100.0,
        },
    )
    supported = replace(
        _plan("ETHUSDT", "EP:SUPPORTED", 10 * MIN),
        evidence={
            "interaction_time_ns": 10 * MIN,
            "directional_posterior_support_rank": 1,
            "directional_family_transition_rank": 2,
            "counterfactual_ownership_per_risk": 1.0,
        },
    )

    assert LiquidityEpisodeCoordinator._arbitrate([opposed, supported]) == [supported]
    assert LiquidityEpisodeCoordinator._arbitrate([opposed]) == [opposed]


def test_independent_and_common_roles_do_not_create_automatic_total_order() -> None:
    independent = replace(
        _plan("BTCUSDT", "EP:INDEPENDENT", 10 * MIN),
        evidence={
            "interaction_time_ns": 10 * MIN,
            "directional_posterior_support_rank": 1,
            "directional_family_transition_rank": 1,
            "counterfactual_ownership_per_risk": 1.0,
            "cross_market_ownership_mode": "INDEPENDENT_LOCAL_TRANSFER",
            "cross_market_event_direction_rank": 1,
        },
    )
    common = replace(
        _plan("ETHUSDT", "EP:COMMON", 10 * MIN),
        evidence={
            "interaction_time_ns": 10 * MIN,
            "directional_posterior_support_rank": 1,
            "directional_family_transition_rank": 1,
            "counterfactual_ownership_per_risk": 2.0,
            "cross_market_ownership_mode": "COMMON_CASCADE",
            "cross_market_event_direction_rank": 4,
        },
    )

    assert LiquidityEpisodeCoordinator._arbitrate([independent, common]) == [common]


def test_execution_rejection_terminalizes_winner_and_offers_next_across_restart() -> None:
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    policies = {symbol: SymbolEpisodePolicy(symbol, 0.1) for symbol in symbols}
    coordinator = LiquidityEpisodeCoordinator(policies)
    winner = replace(
        _plan("BTCUSDT", "EP:TOO-LARGE", 10 * MIN),
        evidence={
            "interaction_time_ns": 10 * MIN,
            "directional_posterior_support_rank": 1,
            "directional_family_transition_rank": 2,
            "counterfactual_ownership_per_risk": 2.0,
        },
    )
    next_plan = replace(
        _plan("ETHUSDT", "EP:EXECUTABLE", 10 * MIN),
        evidence={
            "interaction_time_ns": 10 * MIN,
            "directional_posterior_support_rank": 1,
            "directional_family_transition_rank": 1,
            "counterfactual_ownership_per_risk": 1.0,
        },
    )
    policies["BTCUSDT"]._proposals[winner.episode_id] = winner
    policies["ETHUSDT"]._proposals[next_plan.episode_id] = next_plan

    offered = coordinator.reject_proposal(winner, "MAX_LEVERAGE_EXCEEDED")

    assert offered == [next_plan]
    assert winner.episode_id not in policies["BTCUSDT"]._proposals
    saved = coordinator.export_state()
    restored_policies = {
        symbol: SymbolEpisodePolicy(symbol, 0.1) for symbol in symbols
    }
    restored = LiquidityEpisodeCoordinator(restored_policies)
    restored_policies["BTCUSDT"]._proposals[winner.episode_id] = winner
    restored.restore_state(saved)
    assert winner.episode_id not in restored_policies["BTCUSDT"]._proposals
    assert restored_policies["BTCUSDT"]._terminalized_episodes == {
        winner.episode_id: "MAX_LEVERAGE_EXCEEDED",
    }


def test_restore_conflicts_are_atomic_for_terminal_reason_and_claim_metadata() -> None:
    terminal_policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    terminal = _plan("BTCUSDT", "EP:TERMINAL-STATE", 10 * MIN)
    terminal_policy._proposals[terminal.episode_id] = terminal
    terminal_policy.reject_proposal(terminal, "MAX_LEVERAGE_EXCEEDED")
    baseline = terminal_policy.export_state()
    conflict = deepcopy(baseline)
    conflict["terminalized_episodes"][terminal.episode_id] = "MARGIN_REJECTED"

    try:
        terminal_policy.restore_state(conflict)
    except ValueError as exc:
        assert "conflicting terminal reason" in str(exc)
    else:
        raise AssertionError("conflicting terminal overlay must fail")
    assert terminal_policy.export_state() == baseline

    claimed_policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    claimed = _plan("BTCUSDT", "EP:METADATA-STATE", 10 * MIN)
    claimed_policy._proposals[claimed.episode_id] = claimed
    claimed_policy.claim(claimed)
    claimed_baseline = claimed_policy.export_state()
    metadata_conflict = deepcopy(claimed_baseline)
    metadata_conflict["claimed_plan_metadata"][claimed.episode_id]["family"] = (
        "ACCEPTED_AUCTION_CONTINUATION"
    )

    try:
        claimed_policy.restore_state(metadata_conflict)
    except ValueError as exc:
        assert "conflicting claimed metadata" in str(exc)
    else:
        raise AssertionError("conflicting metadata overlay must fail")
    assert claimed_policy.export_state() == claimed_baseline


def test_claimed_pending_plan_is_invalidated_when_source_line_version_supersedes() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    policy.market.five_minute[:] = [bar(index * 5) for index in range(9)]
    policy.market.serial_5m = 8
    book = policy._trend_channel_books[15]
    book.observe_bar(bar(0, interval=15))
    book.observe_bar(bar(15, interval=15))
    old = TrendLineVersion(
        line_id="LINE:OLD-VERSION",
        symbol="BTCUSDT",
        side="HIGH",
        timeframe_minutes=15,
        first_pivot_id="P:OLD:0",
        second_pivot_id="P:OLD:1",
        first_serial=0,
        second_serial=1,
        first_price=100.0,
        second_price=101.0,
        observed_time_ns=book.bars[0].close_time_ns,
        version=1,
    )
    book.lines.append(old)
    pending = replace(
        _plan("BTCUSDT", "EP:OLD-LINE", 20 * MIN),
        source_boundary_id=old.line_id,
        evidence={
            "interaction_time_ns": 20 * MIN,
            "source_timeframe_minutes": 15,
            "source_kind": "DOWNTREND_LINE",
        },
    )
    policy._proposals[pending.episode_id] = pending
    policy.claim(pending)
    decision = bar(40, interval=1)
    old.superseded_time_ns = decision.open_time_ns

    policy._refresh_proposals([], decision)

    assert policy.claimed_plan_validity(pending.plan_id) == (
        False,
        "STRUCTURAL_SOURCE_VERSION_SUPERSEDED",
    )


def test_claimed_pending_plan_tracks_projected_destination_version_lifecycle() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    pending = replace(
        _plan("BTCUSDT", "EP:OLD-DESTINATION", 20 * MIN),
        destination_boundary_id="LINE:OLD-DESTINATION",
        evidence={
            "interaction_time_ns": 20 * MIN,
            "source_kind": "SWING_60M",
            "destination_kind": "DOWNTREND_LINE",
        },
    )
    policy._proposals[pending.episode_id] = pending
    policy.claim(pending)

    policy._refresh_proposals([], bar(40, interval=1))

    assert policy.claimed_plan_validity(pending.plan_id) == (
        False,
        "STRUCTURAL_DESTINATION_VERSION_SUPERSEDED",
    )


def test_previously_owned_projected_source_cannot_reappear_as_route_destination() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    spent = StructuralNode(
        node_id="LINE:SPENT-SOURCE",
        symbol="BTCUSDT",
        side="HIGH",
        kind="DOWNTREND_LINE",
        role=StructureRole.ROUTE_OBSTACLE,
        timeframe_minutes=15,
        observed_time_ns=0,
        lower=104.9,
        upper=105.1,
        anchor_serial=10,
        slope_per_bar=0.0,
    )
    policy._first_touch_time_by_structure[spent.node_id] = 10 * MIN
    watch = EpisodeWatch(
        episode_id="EP:ROUTE-AFTER-SPENT-SOURCE",
        family="FAILED_AUCTION_REVERSAL",
        source=boundary("SOURCE:OTHER", "LOW", 99.0),
        side="LONG",
        state="COMPLETED",
        interaction_serial=10,
        interaction_time_ns=10 * MIN,
        event_extreme=98.0,
        last_update_serial=10,
        last_update_time_ns=11 * MIN,
    )

    with patch.object(policy, "_projected_structural_nodes", return_value=[spent]):
        route = policy._route_nodes(watch, 20 * MIN, 10)

    assert spent.node_id not in {item.node_id for item in route}


def test_objective_roles_ties_and_current_source_exclusion_are_deterministic() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    decision_time_ns = 30 * MIN
    source = boundary("SOURCE:15M", "LOW", 99.0)
    source_objective = add_objective(
        policy,
        "OBJECTIVE:CURRENT-SOURCE",
        "HIGH",
        102.0,
        observed=10 * MIN,
        timeframe=15,
        source_boundary_id=source.boundary_id,
    )
    objective_1m = add_objective(
        policy, "OBJECTIVE:1M", "HIGH", 105.0, observed=10 * MIN, timeframe=1,
    )
    objective_15m = add_objective(
        policy, "OBJECTIVE:15M", "HIGH", 105.0, observed=10 * MIN, timeframe=15,
    )
    objective_5m = add_objective(
        policy, "OBJECTIVE:5M", "HIGH", 107.0, observed=10 * MIN, timeframe=5,
    )
    policy.market.boundary_book.boundaries["CONTEXT:60M"] = boundary(
        "CONTEXT:60M", "HIGH", 104.0, observed=10 * MIN,
    )
    prior_day = replace(
        boundary("CONTEXT:PRIOR-DAY", "HIGH", 108.0, observed=10 * MIN),
        kind="PRIOR_DAY_HIGH",
        timeframe_minutes=1440,
    )
    policy.market.boundary_book.boundaries[prior_day.boundary_id] = prior_day
    line = StructuralNode(
        node_id="LINE:ROUTE-ONLY",
        symbol="BTCUSDT",
        side="HIGH",
        kind="DOWNTREND_LINE_15M",
        role=StructureRole.DESTINATION,
        timeframe_minutes=15,
        observed_time_ns=10 * MIN,
        lower=102.9,
        upper=103.1,
        anchor_serial=6,
    )
    watch = EpisodeWatch(
        episode_id="EP:OBJECTIVE-ROLES",
        family="FAILED_AUCTION_REVERSAL",
        source=source,
        side="LONG",
        state="COMPLETED",
        interaction_serial=5,
        interaction_time_ns=20 * MIN,
        event_extreme=98.0,
        last_update_serial=6,
        last_update_time_ns=decision_time_ns,
    )

    with patch.object(policy, "_projected_structural_nodes", return_value=[line]):
        nodes = policy._route_nodes(watch, decision_time_ns, 6, 100.0)

    by_id = {node.node_id: node for node in nodes}
    assert source_objective.boundary_id not in by_id
    assert "CONTEXT:60M" not in by_id
    assert prior_day.boundary_id not in by_id
    assert by_id[line.node_id].role is StructureRole.ROUTE_OBSTACLE
    assert by_id[objective_1m.boundary_id].role is StructureRole.DESTINATION
    assert by_id[objective_5m.boundary_id].role is StructureRole.DESTINATION
    assert by_id[objective_15m.boundary_id].lower == 104.9
    assert policy.market.objective_book.objectives[
        objective_15m.boundary_id
    ].price == 105.0
    route = destination_first_geometry(
        side="LONG",
        source=policy._source_node(watch, 6),
        nodes=nodes,
        entry=100.0,
        stop=99.0,
        decision_time_ns=decision_time_ns,
        serial=6,
    )
    assert route.destination is not None
    assert route.destination.node_id == objective_15m.boundary_id
    assert route.target == 104.9
    assert route.route_obstacle is not None
    assert route.route_obstacle.node_id == line.node_id


def test_horizontal_objective_tp_buffer_is_symmetric_by_side() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    decision_time_ns = 30 * MIN
    high = add_objective(
        policy, "OBJECTIVE:HIGH-BUFFER", "HIGH", 105.0,
        observed=10 * MIN, timeframe=5,
    )
    low = add_objective(
        policy, "OBJECTIVE:LOW-BUFFER", "LOW", 95.0,
        observed=10 * MIN, timeframe=5,
    )

    for side, source_side, expected_id, expected_target in (
        ("LONG", "LOW", high.boundary_id, 104.9),
        ("SHORT", "HIGH", low.boundary_id, 95.1),
    ):
        watch = EpisodeWatch(
            episode_id=f"EP:{side}-BUFFER",
            family="FAILED_AUCTION_REVERSAL",
            source=boundary(f"SOURCE:{side}-BUFFER", source_side, 100.0),
            side=side,
            state="COMPLETED",
            interaction_serial=5,
            interaction_time_ns=20 * MIN,
            event_extreme=99.0 if side == "LONG" else 101.0,
            last_update_serial=6,
            last_update_time_ns=decision_time_ns,
        )
        nodes = policy._route_nodes(
            watch,
            decision_time_ns,
            6,
            100.0,
        )
        destination = next(node for node in nodes if node.node_id == expected_id)
        assert destination.lower == expected_target
        assert destination.upper == expected_target

    assert policy.market.objective_book.objectives[high.boundary_id].price == 105.0
    assert policy.market.objective_book.objectives[low.boundary_id].price == 95.0


def test_new_closer_objective_invalidates_pending_route_across_restart_only() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    plan = replace(
        _plan("BTCUSDT", "EP:OBJECTIVE-RESTART", 10 * MIN),
        evidence={
            "interaction_time_ns": 10 * MIN,
            "source_kind": "SWING_15M",
            "destination_kind": "HORIZONTAL_OBJECTIVE_5M",
        },
    )
    policy._proposals[plan.episode_id] = plan
    policy.claim(plan)
    saved = policy.export_state()

    restored = SymbolEpisodePolicy("BTCUSDT", 0.1)
    restored.restore_state(saved)
    add_objective(
        restored,
        plan.destination_boundary_id,
        "HIGH",
        plan.target,
        observed=5 * MIN,
        timeframe=5,
    )
    closer = add_objective(
        restored,
        "OBJECTIVE:NEW-CLOSER",
        "HIGH",
        100.8,
        observed=20 * MIN,
        timeframe=1,
    )

    restored._refresh_proposals(
        [],
        bar(30, interval=1, open_=100.3, high=100.5, low=100.2, close=100.4),
    )

    assert restored.claimed_plan_validity(plan.plan_id) == (
        False,
        "ROUTE_CHANGED_BY_NEW_CLOSER_OBJECTIVE",
    )
    assert restored.invalidated_claimed_plans[plan.plan_id][
        "superseding_episode_id"
    ] == "OBJECTIVE_FIRST_ABSORBING_ROUTE"
    selected = restored._terminal_decisions[plan.episode_id]
    assert selected["plan"]["target"] == plan.target
    assert selected["plan"]["destination_boundary_id"] == plan.destination_boundary_id
    assert closer.price != selected["plan"]["target"]


def test_consumed_projected_node_is_not_converted_back_into_source() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    consumed = StructuralNode(
        node_id="CHANNEL:CONSUMED-POINT-FOUR",
        symbol="BTCUSDT",
        side="HIGH",
        kind="ASCENDING_CHANNEL_UPPER",
        role=StructureRole.SOURCE,
        timeframe_minutes=15,
        observed_time_ns=0,
        lower=99.9,
        upper=100.1,
        anchor_serial=2,
        slope_per_bar=0.0,
        consumed_time_ns=5 * MIN,
    )
    interaction = bar(
        10, interval=1, open_=100.0, high=100.5, low=99.9, close=100.4,
    )

    with patch.object(policy, "_projected_structural_nodes", return_value=[consumed]):
        policy._create_boundary_watches(interaction, 2, 1.0, 0.0)

    assert not policy._watches


def test_rising_source_uses_interaction_band_for_event_local_ob_ownership() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    source = LiquidityBoundary(
        boundary_id="LINE:RISING-SOURCE",
        symbol="BTCUSDT",
        side="LOW",
        kind="UPTREND_LINE",
        timeframe_minutes=15,
        observed_time_ns=0,
        lower=99.9,
        upper=100.1,
        price=100.0,
        strength=3.0,
        dynamic_slope_per_bar=1.0,
        anchor_serial=10,
    )
    event = [
        bar(
            10, interval=1, open_=100.2, high=100.3, low=99.9, close=100.0,
        ),
        bar(
            11, interval=1, open_=100.0, high=101.2, low=99.95, close=101.1,
        ),
    ]
    policy.market.one_minute.extend(event)
    policy.market.serial_5m = 13
    watch = EpisodeWatch(
        episode_id="EP:RISING-OB",
        family="FAILED_AUCTION_REVERSAL",
        source=source,
        side="LONG",
        state="COMPLETED",
        interaction_serial=10,
        interaction_time_ns=event[0].open_time_ns,
        event_extreme=99.9,
        last_update_serial=13,
        last_update_time_ns=event[-1].close_time_ns,
        evidence={
            "interaction_source_lower": 99.9,
            "interaction_source_upper": 100.1,
        },
    )

    zone = policy._origin_zone(watch, event[-1])

    assert zone.kind == "SOURCE_ORDER_BLOCK"
    assert zone.lower == 100.0
    assert zone.upper == 100.1


def test_only_account_accepted_proposal_is_claimed_and_cascade_is_deduped() -> None:
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    policies = {symbol: SymbolEpisodePolicy(symbol, 0.1) for symbol in symbols}
    coordinator = LiquidityEpisodeCoordinator(policies)
    cascade_evidence = {
        "interaction_time_ns": 10 * MIN,
        "cascade_id": "CASCADE:EXPLICIT",
    }
    selected = replace(
        _plan("BTCUSDT", "EP:BTC", 10 * MIN), evidence=dict(cascade_evidence),
    )
    same_cascade = replace(
        _plan("ETHUSDT", "EP:ETH", 10 * MIN), evidence=dict(cascade_evidence),
    )
    independent = _plan("SOLUSDT", "EP:SOL", 15 * MIN)
    policies["BTCUSDT"]._proposals[selected.episode_id] = selected
    policies["ETHUSDT"]._proposals[same_cascade.episode_id] = same_cascade
    policies["SOLUSDT"]._proposals[independent.episode_id] = independent

    # Merely being offered to a busy account consumes nothing.
    assert selected.episode_id not in policies["BTCUSDT"]._used_episodes
    assert same_cascade.episode_id not in policies["ETHUSDT"]._used_episodes

    coordinator.claim(selected)

    assert selected.episode_id in policies["BTCUSDT"]._used_episodes
    assert same_cascade.episode_id in policies["ETHUSDT"]._used_episodes
    assert same_cascade.episode_id not in policies["ETHUSDT"]._proposals
    assert independent.episode_id in policies["SOLUSDT"]._proposals
    assert independent.episode_id not in policies["SOLUSDT"]._used_episodes


def test_claim_does_not_suppress_same_bucket_independent_peer_without_cascade_id() -> None:
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    policies = {symbol: SymbolEpisodePolicy(symbol, 0.1) for symbol in symbols}
    coordinator = LiquidityEpisodeCoordinator(policies)
    first = replace(
        _plan("BTCUSDT", "EP:INDEPENDENT-BTC", 10 * MIN),
        evidence={
            "interaction_time_ns": 10 * MIN,
            "cross_market_ownership_mode": "INDEPENDENT_LOCAL_TRANSFER",
        },
    )
    peer = replace(
        _plan("SOLUSDT", "EP:INDEPENDENT-SOL", 10 * MIN),
        evidence={
            "interaction_time_ns": 10 * MIN,
            "cross_market_ownership_mode": "INDEPENDENT_LOCAL_TRANSFER",
        },
    )
    policies[first.symbol]._proposals[first.episode_id] = first
    policies[peer.symbol]._proposals[peer.episode_id] = peer

    coordinator.claim(first)

    assert peer.episode_id in policies[peer.symbol]._proposals
    assert peer.episode_id not in policies[peer.symbol]._used_episodes


def test_newer_accepted_level_durably_invalidates_claimed_pending_plan() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    older_plan = _plan("BTCUSDT", "EP:OLDER", 10 * MIN)
    policy._proposals[older_plan.episode_id] = older_plan
    policy.claim(older_plan)
    owner = EpisodeWatch(
        episode_id="EP:NEWER",
        family="ACCEPTED_AUCTION_CONTINUATION",
        source=boundary("SOURCE:NEWER", "LOW", 101.0, observed=20 * MIN),
        side="LONG",
        state="COMPLETED",
        interaction_serial=4,
        interaction_time_ns=20 * MIN,
        event_extreme=100.0,
        last_update_serial=4,
        last_update_time_ns=21 * MIN,
    )

    policy._supersede_older_same_side(owner, bar(21, interval=1))

    assert policy.claimed_plan_validity(older_plan.plan_id) == (
        False,
        "NEWER_SAME_SIDE_ACCEPTED_LEVEL_SUPERSEDED_PENDING",
    )
    saved = policy.export_state()
    restored = SymbolEpisodePolicy("BTCUSDT", 0.1)
    restored.restore_state(saved)
    assert restored.claimed_plan_validity(older_plan.plan_id) == (
        False,
        "NEWER_SAME_SIDE_ACCEPTED_LEVEL_SUPERSEDED_PENDING",
    )
    assert restored.invalidated_claimed_plans[older_plan.plan_id][
        "superseding_episode_id"
    ] == owner.episode_id


def test_superseded_watch_in_iteration_snapshot_cannot_terminalize_twice() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    decision = bar(30, interval=1)
    newer = EpisodeWatch(
        episode_id="EP:NEWER-IN-SNAPSHOT",
        family="ACCEPTED_AUCTION_CONTINUATION",
        source=boundary("SOURCE:NEWER-SNAPSHOT", "HIGH", 100.0),
        side="LONG",
        state="COMPLETING",
        interaction_serial=4,
        interaction_time_ns=20 * MIN,
        event_extreme=99.0,
        last_update_serial=5,
        last_update_time_ns=decision.open_time_ns - 1,
        evidence={
            "interaction_source_lower": 99.9,
            "interaction_source_upper": 100.1,
        },
    )
    older = EpisodeWatch(
        episode_id="EP:OLDER-IN-SNAPSHOT",
        family="ACCEPTED_AUCTION_CONTINUATION",
        source=boundary("SOURCE:OLDER-SNAPSHOT", "HIGH", 100.0),
        side="LONG",
        state="COMPLETING",
        interaction_serial=2,
        interaction_time_ns=10 * MIN,
        event_extreme=99.0,
        last_update_serial=5,
        last_update_time_ns=decision.open_time_ns - 1,
        evidence={
            "interaction_source_lower": 99.9,
            "interaction_source_upper": 100.1,
        },
    )
    # This order reproduces the production failure: the newer watch completes
    # first and removes the older object from the live mapping, but the older
    # object still exists in list(self._watches.items()).
    policy._watches[newer.episode_id] = newer
    policy._watches[older.episode_id] = older
    completed = SimpleNamespace(
        completed=True,
        family="ACCEPTED_AUCTION_CONTINUATION",
        terminal_state="ACCEPTANCE_FIRST_RESPONSE_COMPLETED",
        completed_states=("ACCEPTANCE_FIRST_RESPONSE_COMPLETED",),
        phase_basis="STRUCTURAL_RANGE",
        control_transfer=True,
        activity_input_known=True,
        flow_input_known=True,
        response_time_ns=decision.close_time_ns,
        response_close=decision.close,
        retest_time_ns=decision.close_time_ns,
        response_required_extreme=decision.high,
        target_fresh=True,
        stop_intact=True,
    )
    ownership = {"event_residual_ownership": 0.01}
    owner_plan = replace(
        _plan("BTCUSDT", newer.episode_id, newer.interaction_time_ns),
        family="ACCEPTED_AUCTION_CONTINUATION",
    )

    with (
        patch.object(policy.journey, "evaluate", return_value=completed),
        patch.object(policy, "_episode_tape", return_value=[decision]),
        patch.object(policy, "_event_ownership", return_value=ownership),
        patch.object(
            policy,
            "_origin_zone",
            return_value=EntryZone(
                "SOURCE_ORDER_BLOCK", 99.5, 100.0, 0, decision.open_time_ns,
            ),
        ),
        patch.object(policy, "_build_plan", return_value=owner_plan) as build,
    ):
        assert policy._advance_watches(
            decision,
            6,
            1.0,
            0.0,
            {"BTCUSDT": [decision]},
        ) == [owner_plan]

    assert build.call_count == 1
    assert older.episode_id not in policy._watches
    assert policy._terminal_decisions[older.episode_id]["terminal_reason"] == (
        "NEWER_SAME_SIDE_LEVEL_SUPERSEDED_PENDING"
    )


def test_restart_overlay_removes_replayed_claim_and_cascade_but_keeps_independent_proposal() -> None:
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    policies = {symbol: SymbolEpisodePolicy(symbol, 0.1) for symbol in symbols}
    coordinator = LiquidityEpisodeCoordinator(policies)
    cascade_evidence = {
        "interaction_time_ns": 10 * MIN,
        "cascade_id": "CASCADE:EXPLICIT",
    }
    selected = replace(
        _plan("BTCUSDT", "EP:BTC", 10 * MIN), evidence=dict(cascade_evidence),
    )
    same_cascade = replace(
        _plan("ETHUSDT", "EP:ETH", 10 * MIN), evidence=dict(cascade_evidence),
    )
    policies["BTCUSDT"]._proposals[selected.episode_id] = selected
    policies["ETHUSDT"]._proposals[same_cascade.episode_id] = same_cascade
    coordinator.claim(selected)
    saved = coordinator.export_state()

    # A clean process causally replays completed bars first.  That replay may
    # reconstruct the old episode proposals plus a still-live independent one.
    restored_policies = {symbol: SymbolEpisodePolicy(symbol, 0.1) for symbol in symbols}
    restored = LiquidityEpisodeCoordinator(restored_policies)
    independent = _plan("SOLUSDT", "EP:SOL", 15 * MIN)
    for policy, proposal in (
        (restored_policies["BTCUSDT"], selected),
        (restored_policies["ETHUSDT"], same_cascade),
        (restored_policies["SOLUSDT"], independent),
    ):
        policy._proposals[proposal.episode_id] = proposal
        policy._watches[proposal.episode_id] = EpisodeWatch(
            episode_id=proposal.episode_id,
            family=proposal.family,
            source=boundary(f"SOURCE:{proposal.episode_id}", "LOW", 99.0, symbol=proposal.symbol),
            side=proposal.side,
            state="RECLAIMED",
            interaction_serial=0,
            interaction_time_ns=int(proposal.evidence["interaction_time_ns"]),
            event_extreme=99.0,
            last_update_serial=0,
            last_update_time_ns=proposal.decision_time_ns,
            bars_remaining=1,
        )

    restored.restore_state(saved)
    restored.restore_state(saved)  # Nautilus + SQLite hooks may both restore.
    restored.claim(selected)  # An acceptance event may be redelivered after restart.

    assert restored.export_state() == saved
    assert selected.episode_id not in restored_policies["BTCUSDT"]._proposals
    assert selected.episode_id not in restored_policies["BTCUSDT"]._watches
    assert same_cascade.episode_id not in restored_policies["ETHUSDT"]._proposals
    assert same_cascade.episode_id not in restored_policies["ETHUSDT"]._watches
    assert independent.episode_id in restored_policies["SOLUSDT"]._proposals
    assert independent.episode_id in restored_policies["SOLUSDT"]._watches
    assert restored_policies["BTCUSDT"]._claimed_plans == {
        selected.episode_id: selected.plan_id,
    }
    assert restored_policies["ETHUSDT"]._claimed_plans == {}


def test_restored_used_identity_prevents_episode_recreation_before_replay() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    interaction = bar(100, low=98.0, close=100.5)
    source = boundary("SOURCE", "LOW", 99.0)
    episode_id = stable_id(
        "BTCUSDT", source.boundary_id, interaction.open_time_ns, "FAILED", prefix="EP:"
    )
    policy.restore_state(
        {
            "version": 1,
            "symbol": "BTCUSDT",
            "used_episode_ids": [episode_id],
            "claimed_plan_ids": {episode_id: "PLAN:used"},
            "last_plan_time_ns": interaction.close_time_ns,
        }
    )

    # Match the stable identity that _start_failed would derive, demonstrating
    # restore-before-replay semantics without checkpointing transient watches.
    policy._start_failed(source, "LONG", interaction, 1, interaction.low, 1.0, 0.0)

    assert episode_id not in policy._watches


def test_restore_rejects_conflicting_claimed_plan_identity_atomically() -> None:
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    policies = {symbol: SymbolEpisodePolicy(symbol, 0.1) for symbol in symbols}
    coordinator = LiquidityEpisodeCoordinator(policies)
    selected = _plan("BTCUSDT", "EP:BTC", 10 * MIN)
    policies["BTCUSDT"]._proposals[selected.episode_id] = selected
    coordinator.claim(selected)
    corrupted = coordinator.export_state()
    corrupted["policies"]["BTCUSDT"]["claimed_plan_ids"][selected.episode_id] = "PLAN:conflict"

    try:
        coordinator.restore_state(corrupted)
    except ValueError as exc:
        assert "conflicting claimed plan" in str(exc)
    else:
        raise AssertionError("conflicting restart identity must fail closed")

    assert policies["BTCUSDT"]._claimed_plans[selected.episode_id] == selected.plan_id
