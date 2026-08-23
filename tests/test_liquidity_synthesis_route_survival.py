from __future__ import annotations

import copy
import hashlib
import json

import pytest

from smc_ict_4.episode_policy_live.domain import Bar, EntryZone, TradePlan
from smc_ict_4.episode_policy_live.route_survival import (
    FrozenRoute,
    RouteIntegrityError,
    RouteState,
    RouteSurvivalBook,
    native_route_economics,
    route_context,
)


MINUTE = 60_000_000_000


def plan(
    plan_id: str,
    *,
    decision_minute: int = 10,
    family: str = "FAILED_AUCTION_REVERSAL",
    selected_side: str = "LONG",
) -> TradePlan:
    decision = decision_minute * MINUTE
    if selected_side == "LONG":
        entry, stop, target = 100.0, 99.0, 102.0
    else:
        entry, stop, target = 100.0, 101.0, 98.0
    return TradePlan(
        episode_id=f"episode-{plan_id}",
        plan_id=plan_id,
        symbol="BTCUSDT",
        family=family,
        side=selected_side,
        decision_time_ns=decision,
        entry=entry,
        stop=stop,
        target=target,
        # Deliberately near: the survival tracker must not expire on this field.
        expires_time_ns=decision + MINUTE,
        source_boundary_id="source",
        destination_boundary_id="target",
        entry_zone=EntryZone("FVG", 99.9, 100.1, decision - MINUTE, decision - MINUTE),
        evidence={
            "entry_execution_instruction": (
                "IMMEDIATE_MARKETABLE_FIRST_RESPONSE"
                if family == "ACCEPTED_AUCTION_CONTINUATION"
                else "RESTING_FUTURE_FIRST_RETURN_LIMIT"
            ),
            "source_kind": "PRIOR_DAY_HIGH",
            "source_timeframe_minutes": 1440,
            "destination_kind": "PIVOT_HIGH",
            "destination_timeframe_minutes": 5,
            "event_ownership_role": "LOCAL_LEADER",
            "directional_ownership_category": "INITIATIVE_HELD",
            "higher_timeframe_regime": "ALIGNED",
            "cross_market_ownership_mode": "LOCAL_EXCLUSIVE",
            "inventory_interpretation": "FRESH_SPONSORSHIP_CROWDING",
            "inventory_regime": "OI_EXPANSION",
            "journey_control_pressure": 0.1,
            "journey_control_pressure_surprise": 0.02,
            "journey_control_price_response": 0.01,
            "journey_control_impact_per_pressure": 0.1,
            "cascade_id": "cascade-frozen-before-route",
        },
    )


def bar(
    minute: int,
    *,
    open_: float = 101.0,
    high: float = 101.2,
    low: float = 100.8,
    close: float = 101.0,
) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        interval_minutes=1,
        open_time_ns=(minute - 1) * MINUTE,
        close_time_ns=minute * MINUTE,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=10,
        quote_volume=1_000,
        taker_buy_quote_volume=550,
        trade_count=10,
    )


def test_native_economics_uses_structural_three_percent_then_adds_costs() -> None:
    economics = native_route_economics(plan("econ"), tick_size="0.1")
    assert economics.gross_rr == pytest.approx(2.0)
    assert economics.target_nav_return < 0.03 * 2.0
    assert economics.stop_nav_return < -0.03
    assert economics.adverse_stop_fill == pytest.approx(98.7)
    expected_p = -economics.stop_log_growth / (
        economics.target_log_growth - economics.stop_log_growth
    )
    assert economics.conditional_break_even_p == pytest.approx(expected_p)
    assert len(economics.assumptions_id) == 64


def test_context_is_categorical_and_excludes_symbol_date_and_side() -> None:
    long_context = route_context(plan("long", selected_side="LONG"))
    short_context = route_context(plan("short", selected_side="SHORT"))
    assert long_context == short_context
    joined = "|".join(long_context)
    assert "BTCUSDT" not in joined
    assert "LONG" not in joined
    assert "SHORT" not in joined
    assert "LOCAL_LEADER" in joined
    assert "INITIATIVE_HELD" in joined
    assert "ALIGNED" in joined
    assert "LOCAL_EXCLUSIVE" in joined
    assert "FRESH_SPONSORSHIP_CROWDING:OI_EXPANSION" in joined
    assert "PIVOT_HIGH@5" in joined
    assert "LOG2_OCTAVE" in joined


def test_accepted_route_is_filled_at_decision_but_decision_bar_is_not_consumed() -> None:
    book = RouteSurvivalBook()
    route = book.register(
        plan("accepted", family="ACCEPTED_AUCTION_CONTINUATION"),
        selected=True,
    )
    assert route.state == RouteState.FILLED
    assert route.fill_time_ns == route.decision_time_ns
    assert book.score(route).fill_probability == 1.0
    same = book.observe("accepted", bar(10, high=103, low=98, open_=100, close=100))
    assert same.state == RouteState.FILLED
    resolved = book.observe("accepted", bar(11, high=102.1, low=100, open_=101, close=102))
    assert resolved.state == RouteState.TARGET_FIRST


def test_resting_fill_bar_never_credits_target_and_stop_is_adverse_first() -> None:
    book = RouteSurvivalBook()
    book.register(plan("target-on-fill"), selected=False)
    filled = book.observe(
        "target-on-fill",
        bar(11, open_=101, high=102.2, low=99.5, close=101.5),
    )
    assert filled.state == RouteState.FILLED
    assert filled.postfill_continue_bars == 1

    book.register(plan("stop-on-fill"), selected=True)
    stopped = book.observe(
        "stop-on-fill",
        bar(11, open_=101, high=102.2, low=98.9, close=100),
    )
    assert stopped.state == RouteState.STOP_FIRST


def test_pending_target_spent_without_entry_is_no_fill() -> None:
    book = RouteSurvivalBook()
    book.register(plan("spent"), selected=False)
    resolved = book.observe(
        "spent",
        bar(11, open_=102.2, high=102.5, low=102.1, close=102.3),
    )
    assert resolved.state == RouteState.NO_FILL
    assert resolved.fill_time_ns is None


def test_same_bar_price_fill_and_stop_own_race_against_intrinsic_invalidation() -> None:
    book = RouteSurvivalBook()
    book.register(plan("price-first"), selected=False)
    resolved = book.observe(
        "price-first",
        bar(11, open_=100.5, high=101, low=98.8, close=99.2),
        intrinsic_invalidated=True,
    )
    assert resolved.state == RouteState.STOP_FIRST
    assert resolved.fill_time_ns == 11 * MINUTE


def test_directional_barriers_catch_gaps_beyond_stop_and_target() -> None:
    stop_book = RouteSurvivalBook()
    stop_book.register(
        plan("gap-stop", family="ACCEPTED_AUCTION_CONTINUATION"),
        selected=True,
    )
    stopped = stop_book.observe(
        "gap-stop",
        bar(11, open_=98.5, high=98.8, low=98.0, close=98.4),
    )
    assert stopped.state == RouteState.STOP_FIRST

    target_book = RouteSurvivalBook()
    target_book.register(
        plan("gap-target", family="ACCEPTED_AUCTION_CONTINUATION"),
        selected=True,
    )
    targeted = target_book.observe(
        "gap-target",
        bar(11, open_=102.5, high=103, low=102.2, close=102.7),
    )
    assert targeted.state == RouteState.TARGET_FIRST


def test_later_bar_touching_target_and_stop_is_stop_first() -> None:
    book = RouteSurvivalBook()
    book.register(plan("ambiguous"), selected=False)
    assert book.observe("ambiguous", bar(11, low=99.9)).state == RouteState.FILLED
    resolved = book.observe(
        "ambiguous",
        bar(12, open_=100, high=102.2, low=98.8, close=100),
    )
    assert resolved.state == RouteState.STOP_FIRST


def test_intrinsic_invalidation_only_terminates_pending_and_expiry_is_ignored() -> None:
    book = RouteSurvivalBook()
    book.register(plan("pending"), selected=False)
    # This is already later than expires_time_ns, but remains naturally pending.
    continued = book.observe("pending", bar(12), intrinsic_invalidated=False)
    assert continued.state == RouteState.PENDING
    assert continued.prefill_continue_bars == 1
    no_fill = book.observe("pending", bar(13), intrinsic_invalidated=True)
    assert no_fill.state == RouteState.NO_FILL

    book.register(plan("filled", decision_minute=20), selected=False)
    assert book.observe("filled", bar(21, low=99.9)).state == RouteState.FILLED
    still_filled = book.observe("filled", bar(22), intrinsic_invalidated=True)
    assert still_filled.state == RouteState.FILLED


def test_selected_and_unselected_routes_have_identical_lifecycle() -> None:
    chosen = RouteSurvivalBook()
    shadow = RouteSurvivalBook()
    left = chosen.register(plan("same"), selected=True)
    right = shadow.register(plan("same"), selected=False)
    assert left.context == right.context
    sequence = [bar(11), bar(12, low=99.9), bar(13), bar(14, high=102.1)]
    for item in sequence:
        left = chosen.observe("same", item)
        right = shadow.observe("same", item)
        assert left.state == right.state
        assert left.prefill_continue_bars == right.prefill_continue_bars
        assert left.postfill_continue_bars == right.postfill_continue_bars
    assert left.state == RouteState.TARGET_FIRST


def test_cold_route_is_exactly_null_then_resolved_outcomes_move_log_growth() -> None:
    book = RouteSurvivalBook()
    cold = book.register(plan("cold"), selected=False)
    cold_score = book.score(cold)
    assert cold_score.fill_probability == pytest.approx(0.5)
    assert cold_score.target_given_fill_probability == pytest.approx(
        cold.economics.conditional_break_even_p
    )
    assert cold_score.expected_log_growth == 0.0
    assert cold_score.action == "NULL"

    book.observe("cold", bar(11, low=99.9))
    book.observe("cold", bar(12, high=102.1))
    candidate = book.register(plan("after-win", decision_minute=20), selected=False)
    score = book.score(candidate)
    assert score.expected_log_growth > 0
    assert score.action == "TAKE"
    assert score.duration_observations == 1
    assert score.expected_slot_minutes > 0
    assert score.growth_per_expected_slot_minute == pytest.approx(
        score.expected_log_growth / score.expected_slot_minutes
    )


def test_continue_exposures_active_age_and_censor_survive_restart() -> None:
    book = RouteSurvivalBook()
    book.register(plan("restart"), selected=False)
    book.observe("restart", bar(11))
    book.observe("restart", bar(12, low=99.9))
    route = book.observe("restart", bar(13))
    assert route.prefill_continue_bars == 1
    assert route.postfill_continue_bars == 2
    assert route.active_age_minutes == pytest.approx(3)
    live_score = book.score(route)
    assert live_score.phase == "FILLED"
    assert live_score.active_age_minutes == pytest.approx(3)
    assert live_score.expected_remaining_slot_minutes > 0
    assert live_score.expected_slot_minutes == pytest.approx(
        live_score.active_age_minutes + live_score.expected_remaining_slot_minutes
    )

    payload = book.export_state()
    restored = RouteSurvivalBook.restore(payload)
    assert restored.export_state() == payload
    assert restored.route("restart") == route
    censored = restored.right_censor("restart", time_ns=14 * MINUTE)
    assert censored.state == RouteState.RIGHT_CENSORED
    # Censoring is not converted into a Bernoulli failure.
    other = restored.register(plan("other", decision_minute=20), selected=False)
    assert restored.score(other).expected_log_growth == 0.0


def test_duplicates_mutations_and_corrupt_restart_fail_closed() -> None:
    book = RouteSurvivalBook()
    original = plan("integrity")
    book.register(original, selected=False)
    with pytest.raises(RouteIntegrityError, match="duplicate route"):
        book.register(original, selected=False)
    mutated = TradePlan.from_dict({**original.to_dict(), "target": 103.0})
    with pytest.raises(RouteIntegrityError, match="route mutation"):
        book.register(mutated, selected=False)

    book.observe("integrity", bar(11))
    with pytest.raises(RouteIntegrityError, match="duplicate observation"):
        book.observe("integrity", bar(11))

    changed_bar = bar(11, close=101.1)
    with pytest.raises(RouteIntegrityError, match="observation mutation"):
        book.observe("integrity", changed_bar)

    payload = book.export_state()
    corrupt = copy.deepcopy(payload)
    corrupt["routes"][0]["entry"] = 777.0
    with pytest.raises(RouteIntegrityError, match="checksum"):
        RouteSurvivalBook.restore(corrupt)

    # A checksum is an accidental-corruption guard, not authority: semantic
    # validation still rejects a consistently re-hashed route mutation.
    corrupt = copy.deepcopy(payload)
    corrupt["routes"][0]["entry"] = 777.0
    body = {key: value for key, value in corrupt.items() if key != "checksum"}
    corrupt["checksum"] = hashlib.sha256(
        json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(RouteIntegrityError, match="route mutation"):
        RouteSurvivalBook.restore(corrupt)


def test_resolution_ids_are_stable_and_unique() -> None:
    first = RouteSurvivalBook()
    second = RouteSurvivalBook()
    for book in (first, second):
        book.register(plan("stable"), selected=False)
        book.observe("stable", bar(11, low=99.9))
        book.observe("stable", bar(12, high=102.1))
    left = first.route("stable")
    right = second.route("stable")
    assert left.resolution_id == right.resolution_id
    assert left.resolution_id is not None
    assert left.resolution_id.startswith("ROUTE-RES:")


def test_parent_backoff_concentration_is_not_a_tunable_gate() -> None:
    assert RouteSurvivalBook(backoff_concentration=1.0).backoff_concentration == 1.0
    with pytest.raises(ValueError, match="fixed at exactly one"):
        RouteSurvivalBook(backoff_concentration=2.0)
