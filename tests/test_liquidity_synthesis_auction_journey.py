"""Contracts for the ported event-time auction-journey mechanisms."""
from __future__ import annotations

from dataclasses import replace
import random

import pytest

from smc_ict_4.episode_policy_live.auction_journey import (
    ACCEPTANCE_FIRST_RESPONSE_RULE,
    CausalJourneyRegistry,
    EventTimeAuctionJourney,
    PROVENANCE,
    StructureInteraction,
)
from smc_ict_4.episode_policy_live.domain import Bar


MINUTE = 60_000_000_000


def bar(
    minute: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    quote: float = 100.0,
    buy: float = 50.0,
    volume: float = 1.0,
) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        interval_minutes=1,
        open_time_ns=minute * MINUTE,
        close_time_ns=(minute + 1) * MINUTE - 1,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        quote_volume=quote,
        taker_buy_quote_volume=buy,
        trade_count=10,
    )


def interaction(at: int = 6) -> StructureInteraction:
    return StructureInteraction(
        structure_id="PUBLIC:HIGH:1",
        symbol="BTCUSDT",
        source_side="HIGH",
        lower=99.9,
        upper=100.0,
        interaction_time_ns=(at + 1) * MINUTE - 1,
    )


def tape() -> EventTimeAuctionJourney:
    journey = EventTimeAuctionJourney("BTCUSDT", 0.1)
    for minute in range(6):
        journey.observe(
            bar(
                minute,
                open_=99.4,
                high=99.7,
                low=99.2,
                close=99.5,
                quote=100.0,
                buy=50.0,
            )
        )
    return journey


def test_failed_auction_is_sweep_reclaim_hold_then_inward_control() -> None:
    journey = tape()
    episode = (
        bar(6, open_=99.8, high=100.5, low=99.7, close=100.2, buy=80.0),
        bar(7, open_=100.2, high=100.25, low=99.75, close=99.85, buy=25.0),
        bar(8, open_=99.85, high=99.9, low=99.1, close=99.2, buy=20.0),
    )
    for item in episode:
        journey.observe(item)

    result = journey.evaluate(interaction(), episode[-1].close_time_ns)

    assert result.completed
    assert result.family == "FAILED_AUCTION_REVERSAL"
    assert result.settlement == "SETTLED_INSIDE"
    assert result.control_transfer
    assert result.completed_states == (
        "OUTWARD_SWEEP",
        "RECLAIM",
        "HELD_INSIDE",
        "CONTROL_TRANSFER",
    )
    assert result.phase_basis == "TRADED_ACTIVITY"
    assert result.provenance == PROVENANCE


def test_accepted_auction_is_break_retest_then_immediate_response_completion() -> None:
    journey = tape()
    episode = (
        bar(6, open_=99.8, high=100.6, low=99.7, close=100.5, buy=80.0),
        bar(7, open_=100.5, high=100.55, low=99.95, close=100.2, buy=40.0),
        bar(8, open_=100.2, high=101.2, low=100.1, close=101.0, buy=85.0),
    )
    for item in episode:
        journey.observe(item)

    result = journey.evaluate(interaction(), episode[-1].close_time_ns)

    assert result.completed
    assert result.family == "ACCEPTED_AUCTION_CONTINUATION"
    assert result.settlement == "SETTLED_OUTSIDE"
    assert result.control_transfer
    assert result.completed_states == (
        "OUTSIDE_BREAK",
        "FIRST_RETEST",
        "FIRST_RESPONSE_CONFIRMED",
        "CONTROL_TRANSFER",
    )
    assert result.terminal_state == "ACCEPTED_AUCTION_FIRST_RESPONSE_COMPLETED"
    assert result.retest_time_ns == episode[1].close_time_ns
    assert result.response_time_ns == episode[2].close_time_ns
    assert result.response_required_extreme == episode[1].high
    assert "easychart_re1_reaction.py@1142aca" in result.provenance
    assert "FIRST_RETEST_REQUIRES_NEXT_COMPLETED_MICRO_CLOSE" in ACCEPTANCE_FIRST_RESPONSE_RULE
    assert result.blocks[-1].progress > 0.0
    assert result.control_flow_share is not None and result.control_flow_share > 0.0


def test_defended_journey_is_disjoint_from_a_sweep() -> None:
    journey = tape()
    episode = (
        bar(6, open_=99.7, high=100.0, low=99.6, close=99.8, buy=35.0),
        bar(7, open_=99.8, high=99.85, low=99.2, close=99.3, buy=20.0),
    )
    for item in episode:
        journey.observe(item)

    result = journey.evaluate(interaction(), episode[-1].close_time_ns)

    assert result.completed
    assert result.family == "DEFENDED_AUCTION_CONTINUATION"
    assert "OUTWARD_SWEEP" not in result.completed_states
    assert result.completed_states == (
        "BOUNDARY_TOUCH",
        "COMPLETED_RESPONSE",
        "CONTROL_TRANSFER",
    )


def test_low_side_uses_the_same_causal_journey_in_mirrored_coordinates() -> None:
    journey = EventTimeAuctionJourney("BTCUSDT", 0.1)
    for minute in range(6):
        journey.observe(bar(minute, open_=100.5, high=100.8, low=100.3, close=100.5))
    source = StructureInteraction(
        structure_id="PUBLIC:LOW:1",
        symbol="BTCUSDT",
        source_side="LOW",
        lower=100.0,
        upper=100.1,
        interaction_time_ns=7 * MINUTE - 1,
    )
    episode = (
        bar(6, open_=100.2, high=100.3, low=99.4, close=99.5, buy=20.0),
        bar(7, open_=99.5, high=100.05, low=99.45, close=99.8, buy=60.0),
        bar(8, open_=99.8, high=99.9, low=98.8, close=99.0, buy=15.0),
    )
    for item in episode:
        journey.observe(item)

    result = journey.evaluate(source, episode[-1].close_time_ns)

    assert result.completed
    assert result.family == "ACCEPTED_AUCTION_CONTINUATION"
    assert result.control_transfer
    assert result.blocks[-1].progress > 0.0


def test_journey_does_not_expire_after_a_fixed_number_of_bars() -> None:
    journey = tape()
    journey.observe(bar(6, open_=99.8, high=100.5, low=99.7, close=100.2, buy=80.0))
    journey.observe(bar(7, open_=100.2, high=100.25, low=99.8, close=99.9, buy=30.0))
    for minute in range(8, 308):
        journey.observe(
            bar(
                minute,
                open_=99.9,
                high=99.95,
                low=99.85,
                close=99.9,
                buy=50.0,
            )
        )
    last = bar(308, open_=99.9, high=99.9, low=99.0, close=99.1, buy=15.0)
    journey.observe(last)

    result = journey.evaluate(interaction(), last.close_time_ns)

    assert result.completed
    assert result.family == "FAILED_AUCTION_REVERSAL"
    assert sum(block.bars for block in result.blocks) >= 303


def test_missing_activity_and_flow_stay_unknown_under_structural_phase_fallback() -> None:
    journey = tape()
    episode = (
        bar(6, open_=99.7, high=100.0, low=99.6, close=99.8, quote=0.0, buy=0.0, volume=0.0),
        bar(7, open_=99.8, high=99.85, low=99.2, close=99.3, quote=0.0, buy=0.0, volume=0.0),
    )
    for item in episode:
        journey.observe(item)

    result = journey.evaluate(interaction(), episode[-1].close_time_ns)

    assert result.completed
    assert result.family == "DEFENDED_AUCTION_CONTINUATION"
    assert result.phase_basis == "STRUCTURAL_RANGE"
    assert not result.activity_input_known
    assert not result.flow_input_known
    assert result.control_flow_share is None
    assert all(block.activity_ratio is None and block.flow_share is None for block in result.blocks)


def test_decision_timestamp_cannot_use_later_completion_bar() -> None:
    journey = tape()
    break_bar = bar(6, open_=99.8, high=100.6, low=99.7, close=100.5, buy=80.0)
    return_bar = bar(7, open_=100.5, high=100.55, low=99.95, close=100.2, buy=40.0)
    later = bar(8, open_=100.2, high=101.2, low=100.1, close=101.0, buy=85.0)
    for item in (break_bar, return_bar, later):
        journey.observe(item)

    before = journey.evaluate(interaction(), return_bar.close_time_ns)
    after = journey.evaluate(interaction(), later.close_time_ns)

    assert not before.completed
    assert before.family == "ACCEPTED_AUCTION_CONTINUATION"
    assert before.terminal_state == "ACCEPTANCE_WAITING_FIRST_RESPONSE"
    assert after.family == "ACCEPTED_AUCTION_CONTINUATION"


def test_failed_first_response_is_terminal_even_if_a_later_bar_breaks_out() -> None:
    journey = tape()
    break_bar = bar(6, open_=99.8, high=100.6, low=99.7, close=100.5, buy=80.0)
    retest = bar(7, open_=100.5, high=100.55, low=99.95, close=100.2, buy=40.0)
    failed_response = bar(8, open_=100.2, high=101.1, low=100.1, close=100.5, buy=80.0)
    later_breakout = bar(9, open_=100.5, high=102.0, low=100.4, close=101.8, buy=90.0)
    for item in (break_bar, retest, failed_response, later_breakout):
        journey.observe(item)

    first = journey.evaluate(interaction(), failed_response.close_time_ns)
    later = journey.evaluate(interaction(), later_breakout.close_time_ns)

    assert not first.completed
    assert first.terminal_state == "ACCEPTANCE_FIRST_RESPONSE_FAILED"
    assert first.completed_states[-1] == "FIRST_RESPONSE_FAILED"
    assert first.response_close == failed_response.close
    assert first.response_required_extreme == retest.high
    assert later == first
    assert later.observed_time_ns == failed_response.close_time_ns
    assert later.blocks[-1].end_ns == failed_response.close_time_ns


def test_first_response_requires_close_not_intrabar_excursion_beyond_retest() -> None:
    journey = tape()
    episode = (
        bar(6, open_=99.8, high=100.6, low=99.7, close=100.5, buy=80.0),
        bar(7, open_=100.5, high=100.55, low=99.95, close=100.2, buy=40.0),
        bar(8, open_=100.2, high=101.4, low=100.1, close=100.55, buy=80.0),
    )
    for item in episode:
        journey.observe(item)

    result = journey.evaluate(interaction(), episode[-1].close_time_ns)

    assert not result.completed
    assert result.terminal_state == "ACCEPTANCE_FIRST_RESPONSE_FAILED"
    assert result.response_close == result.response_required_extreme


def test_first_response_target_and_stop_touches_precede_close_confirmation() -> None:
    journey = tape()
    episode = (
        bar(6, open_=99.8, high=100.6, low=99.7, close=100.5, buy=80.0),
        bar(7, open_=100.5, high=100.55, low=99.95, close=100.2, buy=40.0),
        bar(8, open_=100.2, high=101.2, low=99.8, close=100.9, buy=80.0),
    )
    for item in episode:
        journey.observe(item)

    target = journey.evaluate(
        interaction(), episode[-1].close_time_ns, stop=99.0, target=101.0
    )
    stop = journey.evaluate(
        interaction(), episode[-1].close_time_ns, stop=99.9, target=102.0
    )
    both = journey.evaluate(
        interaction(), episode[-1].close_time_ns, stop=99.9, target=101.0
    )

    assert target.terminal_state == "ACCEPTANCE_TARGET_SPENT_ON_FIRST_RESPONSE"
    assert not target.completed and target.target_fresh is False
    assert stop.terminal_state == "ACCEPTANCE_STOP_TOUCHED_ON_FIRST_RESPONSE"
    assert not stop.completed and stop.stop_intact is False
    # RE1 checks destination before stop when both trade intrabar.
    assert both.terminal_state == "ACCEPTANCE_TARGET_SPENT_ON_FIRST_RESPONSE"


def test_stop_or_destination_already_traded_invalidates_completed_journey() -> None:
    journey = tape()
    episode = (
        bar(6, open_=99.8, high=100.5, low=99.7, close=100.2, buy=80.0),
        bar(7, open_=100.2, high=100.25, low=99.75, close=99.85, buy=25.0),
        bar(8, open_=99.85, high=99.9, low=99.1, close=99.2, buy=20.0),
    )
    for item in episode:
        journey.observe(item)

    stopped = journey.evaluate(interaction(), episode[-1].close_time_ns, stop=100.4)
    spent = journey.evaluate(interaction(), episode[-1].close_time_ns, target=99.5)

    assert not stopped.completed
    assert stopped.terminal_state == "STOP_ALREADY_INVALIDATED"
    assert not spent.completed
    assert spent.terminal_state == "DESTINATION_ALREADY_SPENT"


def test_one_simultaneous_overlapping_structure_interaction_has_one_owner() -> None:
    journey = tape()
    episode = (
        bar(6, open_=99.8, high=100.5, low=99.7, close=100.2, buy=80.0),
        bar(7, open_=100.2, high=100.25, low=99.75, close=99.85, buy=25.0),
        bar(8, open_=99.85, high=99.9, low=99.1, close=99.2, buy=20.0),
    )
    for item in episode:
        journey.observe(item)
    evidence = journey.evaluate(interaction(), episode[-1].close_time_ns)
    registry = CausalJourneyRegistry(0.1)

    claimed = registry.claim(interaction(), evidence, "PLAN:1")
    same_band = replace(interaction(), structure_id="PUBLIC:HIGH:OVERLAP", lower=99.95, upper=100.05)
    later_band = replace(same_band, interaction_time_ns=interaction().interaction_time_ns + MINUTE)

    assert registry.existing_owner(same_band) == claimed
    assert registry.existing_owner(later_band) is None
    with pytest.raises(ValueError, match="different owner"):
        registry.claim(same_band, evidence, "PLAN:2")
    assert registry.claim(interaction(), evidence, "PLAN:1") is claimed


def test_unresolved_observation_cannot_consume_structure_ownership() -> None:
    journey = tape()
    quiet = bar(6, open_=99.5, high=99.7, low=99.3, close=99.5)
    journey.observe(quiet)
    evidence = journey.evaluate(interaction(), quiet.close_time_ns)
    registry = CausalJourneyRegistry(0.1)

    assert not evidence.completed
    with pytest.raises(ValueError, match="completed journey"):
        registry.claim(interaction(), evidence, "PLAN:UNRESOLVED")
    assert registry.claims == ()


def test_ring_time_slice_is_inclusive_and_chronological_after_wrap() -> None:
    journey = EventTimeAuctionJourney("BTCUSDT", 0.1, history_bars=361)
    observed = [
        bar(
            minute,
            open_=99.0 + minute / 100.0,
            high=99.2 + minute / 100.0,
            low=98.8 + minute / 100.0,
            close=99.1 + minute / 100.0,
        )
        for minute in range(400)
    ]
    for item in observed:
        journey.observe(item)

    assert tuple(item.close_time_ns for item in journey.bars) == tuple(
        item.close_time_ns for item in observed[-361:]
    )
    selected = journey.bars_between(
        observed[100].close_time_ns,
        observed[103].close_time_ns,
    )
    assert tuple(item.close_time_ns for item in selected) == tuple(
        item.close_time_ns for item in observed[100:104]
    )
    assert journey.bars_between(
        observed[0].close_time_ns,
        observed[20].close_time_ns,
    ) == ()
    selected = journey.bars_between(
        observed[200].close_time_ns + 1,
        observed[202].close_time_ns - 1,
    )
    assert tuple(item.close_time_ns for item in selected) == (
        observed[201].close_time_ns,
    )


def test_ring_truncation_preserves_history_unavailable_contract() -> None:
    journey = EventTimeAuctionJourney("BTCUSDT", 0.1, history_bars=361)
    observed = [
        bar(minute, open_=99.4, high=99.7, low=99.2, close=99.5)
        for minute in range(400)
    ]
    for item in observed:
        journey.observe(item)

    truncated = StructureInteraction(
        structure_id="PUBLIC:HIGH:TRUNCATED",
        symbol="BTCUSDT",
        source_side="HIGH",
        lower=99.9,
        upper=100.0,
        interaction_time_ns=observed[38].close_time_ns,
    )
    at_oldest = replace(
        truncated,
        structure_id="PUBLIC:HIGH:OLDEST",
        interaction_time_ns=observed[39].close_time_ns,
    )

    unavailable = journey.evaluate(truncated, observed[-1].close_time_ns)
    available = journey.evaluate(at_oldest, observed[-1].close_time_ns)

    assert unavailable.terminal_state == "HISTORY_UNAVAILABLE"
    assert available.terminal_state != "HISTORY_UNAVAILABLE"
    # The prior baseline is absent exactly as in the bounded-deque contract.
    assert available.baseline_range is None


class _ReferenceJourney(EventTimeAuctionJourney):
    def _advance_incremental_state(self, interaction, end_seq):  # type: ignore[no-untyped-def]
        del interaction, end_seq
        return None


def _assert_incremental_semantics_equal(actual, expected) -> None:  # type: ignore[no-untyped-def]
    assert replace(
        actual,
        blocks=expected.blocks,
        control_flow_share=expected.control_flow_share,
    ) == expected
    assert len(actual.blocks) == len(expected.blocks)
    for left, right in zip(actual.blocks, expected.blocks, strict=True):
        assert (left.start_ns, left.end_ns, left.bars) == (
            right.start_ns, right.end_ns, right.bars,
        )
        for name in (
            "progress", "path_efficiency", "close_location",
            "favorable_excursion", "adverse_excursion", "activity_ratio",
            "flow_share",
        ):
            left_value = getattr(left, name)
            right_value = getattr(right, name)
            if left_value is None or right_value is None:
                assert left_value is right_value
            else:
                assert left_value == pytest.approx(right_value, rel=1e-14, abs=1e-14)
    if actual.control_flow_share is None or expected.control_flow_share is None:
        assert actual.control_flow_share is expected.control_flow_share
    else:
        assert actual.control_flow_share == pytest.approx(
            expected.control_flow_share, rel=1e-14, abs=1e-14,
        )


def test_incremental_evaluation_is_reference_semantics_equivalent() -> None:
    rng = random.Random(771_204)
    fast = EventTimeAuctionJourney("BTCUSDT", 0.1, history_bars=500)
    reference = _ReferenceJourney("BTCUSDT", 0.1, history_bars=500)
    observed = []
    price = 100.0
    for minute in range(220):
        move = rng.uniform(-0.8, 0.8)
        close = price + move
        item = bar(
            minute,
            open_=price,
            high=max(price, close) + rng.uniform(0.0, 0.5),
            low=min(price, close) - rng.uniform(0.0, 0.5),
            close=close,
            quote=0.0 if minute == 87 else rng.uniform(20.0, 500.0),
            buy=0.0 if minute == 87 else rng.uniform(0.0, 20.0),
            volume=0.0 if minute == 87 else 1.0,
        )
        # Keep generated taker buy quote physically valid.
        if item.quote_volume and item.taker_buy_quote_volume > item.quote_volume:
            item = replace(item, taker_buy_quote_volume=item.quote_volume / 2.0)
        observed.append(item)
        fast.observe(item)
        reference.observe(item)
        price = close

    cases = (
        StructureInteraction("A", "BTCUSDT", "HIGH", 99.8, 100.2, observed[20].close_time_ns),
        StructureInteraction("B", "BTCUSDT", "LOW", 98.8, 99.1, observed[45].close_time_ns),
        StructureInteraction("C", "BTCUSDT", "HIGH", 101.0, 101.3, observed[80].close_time_ns),
    )
    for source in cases:
        for item in observed:
            if item.close_time_ns < source.interaction_time_ns:
                continue
            _assert_incremental_semantics_equal(
                fast.evaluate(source, item.close_time_ns),
                reference.evaluate(source, item.close_time_ns),
            )


def test_incremental_cache_key_includes_band_and_side() -> None:
    fast = tape()
    reference = _ReferenceJourney("BTCUSDT", 0.1)
    for item in fast.bars:
        reference.observe(
            Bar(
                symbol="BTCUSDT", interval_minutes=1,
                open_time_ns=item.close_time_ns - MINUTE + 1,
                close_time_ns=item.close_time_ns,
                open=item.open, high=item.high, low=item.low, close=item.close,
                volume=1.0, quote_volume=item.activity or 0.0,
                taker_buy_quote_volume=(
                    ((item.signed_flow or 0.0) + (item.activity or 0.0)) / 2.0
                ),
                trade_count=10,
            )
        )
    last = bar(6, open_=99.8, high=100.6, low=99.7, close=100.4, buy=80.0)
    fast.observe(last)
    reference.observe(last)
    sources = (
        interaction(),
        replace(interaction(), lower=100.2, upper=100.3),
        replace(interaction(), source_side="LOW"),
    )
    for source in sources:
        assert fast.evaluate(source, last.close_time_ns) == reference.evaluate(
            source, last.close_time_ns
        )
    assert len(fast._states) == len(sources)


def test_incremental_cache_does_not_survive_history_truncation() -> None:
    journey = EventTimeAuctionJourney("BTCUSDT", 0.1, history_bars=361)
    source = StructureInteraction(
        "CACHE:OLD", "BTCUSDT", "HIGH", 99.9, 100.0, 7 * MINUTE - 1,
    )
    for minute in range(20):
        journey.observe(bar(minute, open_=99.5, high=99.8, low=99.2, close=99.5))
    journey.evaluate(source, 20 * MINUTE - 1)
    assert journey._state_key(source) in journey._states

    for minute in range(20, 520):
        journey.observe(bar(minute, open_=99.5, high=99.8, low=99.2, close=99.5))
    unavailable = journey.evaluate(source, 520 * MINUTE - 1)
    assert unavailable.terminal_state == "HISTORY_UNAVAILABLE"
    assert journey._state_key(source) not in journey._states
