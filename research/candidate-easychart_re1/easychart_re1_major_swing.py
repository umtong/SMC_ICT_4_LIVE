"""Single-major-swing liquidity and S/R-flip family for EasyChart RE1.

Repeated-defense areas recover box-range traps, but the supplied material and
the live examples also trade one obvious prior high or low. Such a swing is
not an unconditional bounce signal. Its value is the visible pool of stops
behind the wick and the binary auction which follows when price later reaches
it:

* a sweep outside the swing followed by reclaim is a Fake-out/Trap episode;
* a body break, next-decision-bar hold, detached return and immediate response
  is a genuine S/R-flip continuation episode.

This module adds that mechanism as an independent opportunity family. Only
causally confirmed 15-minute span-6 pivots originate trades. Their original
wick rejection band is the decision area; local span-2 pivots remain available
only as nearer pre-existing objectives. A first later interaction owns the
episode and retires the swing. Every executable entry still requires the
existing one-minute OB, or an FVG overlapping an active OB, followed by the
first detached retest and first-response hold.

For an accepted break, the hard stop is inherited from the causal breakout-wave
origin rather than manufactured immediately behind the retested level. If that
structural stop leaves less than 1.0 gross R to the first pre-existing objective,
the plan is rejected before entry. This preserves the user's immutable-plan
contract without converting normal retest depth into a false high-R setup.

No distance tolerance, score, volatility threshold, fixed-R target, clock
timeout or outcome-dependent selection is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import (
    ObjectKind,
    Pivot,
    ScenarioPath,
    ScenarioSetup,
    StructureFamily,
    StructureZone,
    V5TradePlan,
)
from domain import Candle
from easychart_re1_confirmed import (
    ConfirmedSelectiveScenarioEngine,
    EasyChartRE1ConfirmedBundle,
)
from easychart_zones import ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


SINGLE_MAJOR_SWING_LIQUIDITY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "CONFIRMED_SPAN6_SWING_WICK_BAND_IS_A_SINGLE_MAJOR_LIQUIDITY_STRUCTURE"
)
MAJOR_SWING_EPISODE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIRST_LATER_INTERACTION_OWNS_AND_RETIRES_SINGLE_MAJOR_SWING_LIQUIDITY"
)
MAJOR_SWING_PATH_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "SINGLE_MAJOR_SWING_EXECUTES_ONLY_SWEEP_RECLAIM_OR_CONFIRMED_ACCEPTED_BREAK_RETEST"
)
MAJOR_SWING_ACCEPTANCE_STOP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ACCEPTED_SINGLE_MAJOR_SWING_BREAK_RETEST_USES_CAUSAL_BREAKOUT_WAVE_ORIGIN"
)
for _rule in (
    SINGLE_MAJOR_SWING_LIQUIDITY_RULE,
    MAJOR_SWING_EPISODE_RULE,
    MAJOR_SWING_PATH_RULE,
    MAJOR_SWING_ACCEPTANCE_STOP_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class MajorSwingLiquidityStructureBook(NearestAnyPivotStructureBook):
    """Emit span-6 wick bands as context while retaining all pivots as targets."""

    CONTEXT_SPAN = 6

    def __init__(self, symbol: str, timeframe_minutes: int, tick_size: float) -> None:
        # Span 2 remains in the same causal book only so target_for can select
        # the nearer opposing structure. It never originates a trade here.
        super().__init__(
            symbol,
            timeframe_minutes,
            tick_size,
            pivot_spans=(2, self.CONTEXT_SPAN),
        )

    def _swing_snapshot(self, pivot: Pivot, time_ns: int) -> StructureZone:
        bar = self.bars[pivot.index]
        body_low = min(bar.open, bar.close)
        body_high = max(bar.open, bar.close)
        if pivot.side == "LOW":
            side = ZoneSide.SUPPORT
            kind = ObjectKind.HORIZONTAL_SUPPORT
            lower = pivot.price
            upper = max(body_low, lower + self.tick_size)
            invalidation = pivot.price - self.tick_size
        else:
            side = ZoneSide.RESISTANCE
            kind = ObjectKind.HORIZONTAL_RESISTANCE
            upper = pivot.price
            lower = min(body_high, upper - self.tick_size)
            invalidation = pivot.price + self.tick_size
        return StructureZone(
            zone_id=f"{pivot.pivot_id}:MAJOR_SWING_SNAP:{time_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=side,
            timeframe_minutes=self.timeframe_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=pivot.price,
            formed_index=pivot.index,
            formed_time_ns=pivot.event_time_ns,
            observed_time_ns=pivot.observed_time_ns,
            formation_indices=(pivot.index,),
            strength_ratio=pivot.strength_ratio,
            source_structure_id=pivot.pivot_id,
            source_pivot_span=pivot.span,
            first_touch_index=pivot.first_touch_index,
            first_touch_time_ns=pivot.first_touch_time_ns,
            consumed=bool(
                pivot.consumed
                and pivot.consumed_time_ns is not None
                and pivot.consumed_time_ns < time_ns
            ),
        )

    def boundaries_at(self, time_ns: int) -> list[StructureZone]:
        return [
            self._swing_snapshot(pivot, time_ns)
            for pivot in self._active_pivots.values()
            if pivot.span == self.CONTEXT_SPAN
            and pivot.observed_time_ns < time_ns
        ]

    def snapshot_for(self, zone: StructureZone, time_ns: int) -> StructureZone:
        pivot = self.pivot_for_structure(zone.source_structure_id)
        if pivot is not None and pivot.span == self.CONTEXT_SPAN:
            return self._swing_snapshot(pivot, time_ns)
        return super().snapshot_for(zone, time_ns)


class MajorSwingLiquidityScenarioEngine(ConfirmedSelectiveScenarioEngine):
    """Complete sweep/reclaim and accepted-break policy over one major swing.

    Accepted breaks deliberately inherit the standard non-channel acceptance
    stop: the causal breakout-wave origin, extended beyond the completed entry
    bar only when necessary. A single prior high/low is a structural S/R flip,
    not a zero-width level whose nearest tick is automatically the thesis stop.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = MajorSwingLiquidityStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )


class EasyChartRE1MajorSwingBundle(EasyChartRE1ConfirmedBundle):
    """Response-confirmed diagonal, box-sweep and single-swing families."""

    EXECUTABLE_MAJOR_PATHS = {
        ScenarioPath.REJECTION.value,
        ScenarioPath.ACCEPTANCE.value,
    }

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.major_swing = MajorSwingLiquidityScenarioEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["major_swing"] = 0
        self._major_swing_trace: list[dict[str, Any]] = []

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.major_swing.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.major_swing.plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        existing = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes not in {15, 5, 1}:
            return existing

        raw = self.major_swing.on_bar(timeframe_minutes, bar)
        self._sync_audit("major_swing", self.major_swing)
        output: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if plan.scenario_path not in self.EXECUTABLE_MAJOR_PATHS:
                self._major_swing_trace.append(
                    {
                        "scenario_kind": "major_swing_non_liquidity_path_suppressed",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "scenario_path": plan.scenario_path,
                        "interaction_time_ns": plan.interaction_time_ns,
                        "rule_provenance": MAJOR_SWING_PATH_RULE,
                    },
                )
                continue
            if self._duplicate_episode(plan):
                self._major_swing_trace.append(
                    {
                        "scenario_kind": "major_swing_episode_overlapped_existing_family",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "scenario_path": plan.scenario_path,
                        "interaction_time_ns": plan.interaction_time_ns,
                        "overlap_lower": plan.overlap_lower,
                        "overlap_upper": plan.overlap_upper,
                    },
                )
                continue
            self._claim_episode(plan)
            if self._route_plan(plan):
                output.append(plan)
        return existing + output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            super().drain_trace()
            + self.major_swing.drain_trace()
            + self._major_swing_trace
        )
        self._major_swing_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.major_swing.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["single_major_swing_family"] = {
            "engine": self.major_swing.diagnostics,
            "structure": self.major_swing.structure.diagnostics,
            "context_span": self.major_swing.structure.CONTEXT_SPAN,
            "executable_paths": tuple(sorted(self.EXECUTABLE_MAJOR_PATHS)),
            "rules": (
                SINGLE_MAJOR_SWING_LIQUIDITY_RULE,
                MAJOR_SWING_EPISODE_RULE,
                MAJOR_SWING_PATH_RULE,
                MAJOR_SWING_ACCEPTANCE_STOP_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1MajorSwingBundle
