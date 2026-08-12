from __future__ import annotations

import unittest

from contracts_v5 import ObjectKind, StructureFamily, StructureZone
from domain import Candle, Side
from easychart_zones import ZoneSide
from learned_horizontal_campaign_v8 import (
    CampaignLearnedHorizontalScenarioEngine,
    CampaignPhase,
)
from learned_horizontal_v7 import (
    DefenseInterval,
    LearnedHorizontalZone,
)


def candle(timestamp: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(timestamp, open_, high, low, close, 1.0)


class ObjectiveBook:
    def target_for(self, side: Side, **_: object) -> tuple[StructureZone, float]:
        price = 90.0 if side is Side.SHORT else 120.0
        target_side = ZoneSide.SUPPORT if side is Side.SHORT else ZoneSide.RESISTANCE
        kind = (
            ObjectKind.HORIZONTAL_SUPPORT
            if target_side is ZoneSide.SUPPORT
            else ObjectKind.HORIZONTAL_RESISTANCE
        )
        target = StructureZone(
            zone_id=f"TARGET:{side.name}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=target_side,
            timeframe_minutes=15,
            lower=price,
            upper=price + 0.1,
            invalidation=price - 0.1 if target_side is ZoneSide.SUPPORT else price + 0.2,
            impulse_extreme=price,
            formed_index=0,
            formed_time_ns=0,
            observed_time_ns=0,
            formation_indices=(),
            strength_ratio=1.0,
            source_structure_id=f"TARGET:{side.name}",
            source_pivot_span=1,
        )
        return target, price


def make_engine() -> CampaignLearnedHorizontalScenarioEngine:
    return CampaignLearnedHorizontalScenarioEngine(
        "TEST",
        0.1,
        scale_name="MICRO",
        context_minutes=15,
        trigger_minutes=1,
        objective_book=ObjectiveBook(),
        minimum_gross_rr=1.0,
    )


def learned_zone(
    zone_id: str,
    lower: float,
    upper: float,
    observed_time_ns: int,
    member_times: tuple[int, int],
) -> tuple[LearnedHorizontalZone, tuple[DefenseInterval, DefenseInterval]]:
    member_ids = (f"{zone_id}:A", f"{zone_id}:B")
    item = LearnedHorizontalZone(
        zone_id=zone_id,
        kind=ObjectKind.HORIZONTAL_RESISTANCE,
        family=StructureFamily.HORIZONTAL,
        side=ZoneSide.RESISTANCE,
        timeframe_minutes=15,
        lower=lower,
        upper=upper,
        invalidation=upper + 0.1,
        impulse_extreme=upper,
        formed_index=1,
        formed_time_ns=max(1, observed_time_ns - 1),
        observed_time_ns=observed_time_ns,
        formation_indices=(1, 2),
        strength_ratio=1.5,
        source_structure_id=zone_id,
        source_pivot_span=1,
        touch_count=2,
        member_ids=member_ids,
    )
    intervals = (
        DefenseInterval(
            member_ids[0],
            ZoneSide.RESISTANCE,
            lower - 0.2,
            upper,
            1,
            1,
            member_times[0],
            1,
            1.0,
        ),
        DefenseInterval(
            member_ids[1],
            ZoneSide.RESISTANCE,
            lower,
            upper + 0.2,
            2,
            2,
            member_times[1],
            1,
            1.0,
        ),
    )
    return item, intervals


def inject(
    engine: CampaignLearnedHorizontalScenarioEngine,
    item: LearnedHorizontalZone,
    intervals: tuple[DefenseInterval, DefenseInterval],
) -> None:
    engine.detector.zones.append(item)
    engine.detector._zone_ids.add(item.zone_id)
    engine.detector._active_zones[item.zone_id] = item
    existing = {interval.touch_id for interval in engine.detector.intervals}
    for interval in intervals:
        if interval.touch_id in existing:
            continue
        engine.detector.intervals.append(interval)
        engine.detector._active_intervals[interval.touch_id] = interval


class LearnedHorizontalCampaignTests(unittest.TestCase):
    def _start_short_campaign(self) -> CampaignLearnedHorizontalScenarioEngine:
        engine = make_engine()
        first, intervals = learned_zone("FIRST", 100.0, 101.0, 3, (2, 3))
        inject(engine, first, intervals)
        plans = engine.on_bar(15, candle(10, 100.0, 101.8, 99.0, 99.5))
        self.assertEqual(len(plans), 1)
        campaign = engine.campaigns[Side.SHORT]
        self.assertIs(campaign.phase, CampaignPhase.REVERSAL_ACTIVE)
        self.assertAlmostEqual(campaign.stop_price or 0.0, 101.9)
        return engine

    def test_active_reversal_suppresses_later_same_side_boundary(self) -> None:
        engine = self._start_short_campaign()
        second, intervals = learned_zone("SECOND", 100.5, 101.5, 4, (2, 4))
        inject(engine, second, intervals)
        plans = engine.on_bar(15, candle(20, 100.8, 101.7, 99.5, 100.0))
        self.assertFalse(plans)
        self.assertEqual(len(engine.plans), 1)
        suppressed = [
            setup
            for setup in engine.setups
            if setup.terminal_reason == "campaign_active_same_side_suppressed"
        ]
        self.assertEqual(len(suppressed), 1)

    def test_target_completion_allows_later_boundary_even_with_old_memory(self) -> None:
        engine = self._start_short_campaign()
        engine.on_bar(1, candle(20, 95.0, 100.0, 89.0, 95.0))
        self.assertNotIn(Side.SHORT, engine.campaigns)

        later, intervals = learned_zone("LATER", 100.5, 101.5, 21, (3, 21))
        inject(engine, later, intervals)
        plans = engine.on_bar(15, candle(30, 101.0, 101.7, 99.5, 100.0))
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].higher_zone_id, "LATER")

    def test_stop_locks_stale_boundary_but_fully_fresh_boundary_resets(self) -> None:
        engine = self._start_short_campaign()
        engine.on_bar(1, candle(20, 100.0, 102.5, 95.0, 101.5))
        campaign = engine.campaigns[Side.SHORT]
        self.assertIs(campaign.phase, CampaignPhase.CONTINUATION_LOCK)
        self.assertEqual(campaign.terminal_time_ns, 20)

        stale, stale_intervals = learned_zone("STALE", 103.0, 104.0, 19, (18, 19))
        inject(engine, stale, stale_intervals)
        self.assertFalse(engine.on_bar(15, candle(30, 103.5, 104.5, 102.5, 103.2)))
        self.assertEqual(
            engine.setups[-1].terminal_reason,
            "campaign_stale_boundary_suppressed",
        )

        fresh, fresh_intervals = learned_zone("FRESH", 105.0, 106.0, 23, (22, 23))
        inject(engine, fresh, fresh_intervals)
        plans = engine.on_bar(15, candle(40, 105.5, 106.5, 103.0, 104.5))
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].higher_zone_id, "FRESH")
        self.assertIs(engine.campaigns[Side.SHORT].phase, CampaignPhase.REVERSAL_ACTIVE)

    def test_owner_close_reentry_resets_continuation_lock_only_for_later_setup(self) -> None:
        engine = self._start_short_campaign()
        engine.on_bar(1, candle(20, 100.0, 102.5, 95.0, 101.5))
        self.assertIn(Side.SHORT, engine.campaigns)
        engine.on_bar(15, candle(30, 101.0, 101.5, 98.0, 99.5))
        self.assertNotIn(Side.SHORT, engine.campaigns)

        later, intervals = learned_zone("AFTER_RESET", 103.0, 104.0, 5, (4, 5))
        inject(engine, later, intervals)
        plans = engine.on_bar(15, candle(40, 103.5, 104.5, 102.0, 103.0))
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].higher_zone_id, "AFTER_RESET")

    def test_target_and_stop_in_same_bar_remains_ambiguous_lock(self) -> None:
        engine = self._start_short_campaign()
        engine.on_bar(1, candle(20, 100.0, 102.5, 89.0, 95.0))
        campaign = engine.campaigns[Side.SHORT]
        self.assertIs(campaign.phase, CampaignPhase.AMBIGUOUS_LOCK)
        self.assertEqual(
            campaign.terminal_reason,
            "campaign_ambiguous_target_and_stop_same_bar",
        )

        stale, intervals = learned_zone("AMBIG_STALE", 103.0, 104.0, 19, (18, 19))
        inject(engine, stale, intervals)
        self.assertFalse(engine.on_bar(15, candle(30, 103.5, 104.5, 102.0, 103.0)))
        self.assertEqual(
            engine.setups[-1].terminal_reason,
            "campaign_stale_boundary_suppressed",
        )

    def test_target_resolution_and_new_interaction_same_bar_are_not_reordered(self) -> None:
        engine = self._start_short_campaign()
        second, intervals = learned_zone("SAME_BAR", 100.5, 101.5, 4, (3, 4))
        inject(engine, second, intervals)
        plans = engine.on_bar(15, candle(20, 100.5, 101.7, 89.0, 100.0))
        self.assertFalse(plans)
        self.assertEqual(
            engine.setups[-1].terminal_reason,
            "campaign_same_bar_reordering_suppressed",
        )
        self.assertNotIn(Side.SHORT, engine.campaigns)

    def test_accepted_break_enters_lock_and_suppresses_stale_higher_fade(self) -> None:
        engine = make_engine()
        lower, lower_intervals = learned_zone("LOWER", 100.0, 101.0, 3, (2, 3))
        higher, higher_intervals = learned_zone("HIGHER", 104.0, 105.0, 4, (2, 4))
        inject(engine, lower, lower_intervals)
        inject(engine, higher, higher_intervals)
        self.assertFalse(engine.on_bar(15, candle(10, 100.0, 102.0, 99.0, 101.5)))
        self.assertFalse(engine.on_bar(15, candle(20, 101.5, 103.0, 101.2, 102.0)))
        campaign = engine.campaigns[Side.SHORT]
        self.assertIs(campaign.phase, CampaignPhase.CONTINUATION_LOCK)

        self.assertFalse(engine.on_bar(15, candle(30, 104.5, 105.5, 102.0, 103.5)))
        self.assertEqual(
            engine.setups[-1].terminal_reason,
            "campaign_stale_boundary_suppressed",
        )


if __name__ == "__main__":
    unittest.main()
