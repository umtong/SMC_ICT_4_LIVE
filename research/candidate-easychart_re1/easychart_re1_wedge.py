"""Terminal converging-wave reversal family for EasyChart RE1.

The supplied live example describes a falling wedge/diagonal as progressively
smaller down legs.  The final leg may make one more low, but if it becomes
longer than the preceding down leg the pattern thesis is false.  This gives the
machine a causal invalidation before the outcome is known.

A wedge plan therefore requires:

* two completed same-direction 15-minute legs already shrinking;
* two converging wick trend lines from alternating confirmed pivots;
* a current five-minute terminal sweep of the last low/high which has not yet
  exceeded the preceding leg length;
* a lower-frame OB/FVG event at the terminal area, with the same human-entry
  policy as the main system;
* the pattern-length invalidation and first meaningful five/fifteen-minute
  objective fixed before entry.

The family is a complete countertrend mechanism and is routed independently of
an already established macro direction.  It does not use fitted ratios, ATR,
clock timeouts, scores, partial exits or post-entry changes.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, Pivot, ScenarioPath, SetupState, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_human_policy import EasyChartRE1HumanPolicyBundle, HumanMicroEngine
from easychart_zones import ZoneSide


WEDGE_SHRINKING_LEG_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "TERMINAL_WEDGE_REQUIRES_PROGRESSIVELY_SMALLER_DIRECTIONAL_LEGS_AND_INVALIDATES_WHEN_THE_LAST_LEG_EXCEEDS_THE_PREVIOUS_LEG"
)
WEDGE_CONVERGENCE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "TERMINAL_WEDGE_USES_TWO_CONVERGING_WICK_LINES_AND_ONE_FINAL_LIQUIDITY_SWEEP"
)
for _rule in (WEDGE_SHRINKING_LEG_RULE, WEDGE_CONVERGENCE_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class TerminalWedgeScenarioEngine(HumanMicroEngine):
    """Use the standard execution engine over a causal synthetic wedge boundary."""

    PIVOT_SPAN = 2
    SEARCH_PIVOTS = 14

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._armed_wedge_ids: set[str] = set()
        self._wedge_counts: dict[str, int] = {}

    def _winc(self, key: str) -> None:
        self._wedge_counts[key] = self._wedge_counts.get(key, 0) + 1

    def _pivots_before(self, time_ns: int) -> list[Pivot]:
        pivots = [
            pivot
            for pivot in self.structure.pivots
            if pivot.span == self.PIVOT_SPAN and pivot.observed_time_ns < time_ns
        ]
        return sorted(pivots, key=lambda item: (item.event_time_ns, item.observed_time_ns, item.pivot_id))[-self.SEARCH_PIVOTS :]

    @staticmethod
    def _line_at(first: Pivot, second: Pivot, time_ns: int) -> float:
        if second.event_time_ns <= first.event_time_ns:
            raise RuntimeError("wedge anchors are not chronological")
        slope = (second.price - first.price) / (second.event_time_ns - first.event_time_ns)
        return first.price + slope * (time_ns - first.event_time_ns)

    @staticmethod
    def _slope(first: Pivot, second: Pivot) -> float:
        return (second.price - first.price) / (second.event_time_ns - first.event_time_ns)

    def _latest_pattern(self, pivots: list[Pivot], sides: tuple[str, ...]) -> tuple[Pivot, ...] | None:
        candidates = [combo for combo in combinations(pivots, len(sides)) if tuple(item.side for item in combo) == sides]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda combo: (
                combo[-1].event_time_ns,
                combo[-2].event_time_ns,
                tuple(item.pivot_id for item in combo),
            ),
        )

    def _falling_wedge(self, bar: Candle) -> tuple[str, StructureZone, float] | None:
        pattern = self._latest_pattern(self._pivots_before(bar.ts_close_ns), ("HIGH", "LOW", "HIGH", "LOW", "HIGH"))
        if pattern is None:
            return None
        high1, low1, high2, low2, high3 = pattern
        if not (high1.price > high2.price > high3.price and low1.price > low2.price):
            return None
        leg1 = high1.price - low1.price
        leg2 = high2.price - low2.price
        current_leg = high3.price - bar.low
        if not (leg1 > leg2 > 0.0 and 0.0 < current_leg <= leg2 and bar.low < low2.price):
            return None
        upper_slope = self._slope(high2, high3)
        lower_slope = self._slope(low1, low2)
        if not (upper_slope < lower_slope < 0.0):
            return None
        upper_now = self._line_at(high2, high3, bar.ts_close_ns)
        lower_now = self._line_at(low1, low2, bar.ts_close_ns)
        if not (upper_now > lower_now and bar.low <= lower_now):
            return None

        invalidation = high3.price - leg2
        if bar.low <= invalidation:
            return None
        pattern_id = "FALLING:" + "|".join(item.pivot_id for item in pattern)
        body_low = min(bar.open, bar.close)
        lower = bar.low
        upper = max(body_low, lower + self.tick_size)
        zone = StructureZone(
            zone_id=f"{self.symbol}:15m:TERMINAL_WEDGE:SUPPORT:{pattern_id}:SNAP:{bar.ts_close_ns}",
            kind=ObjectKind.HORIZONTAL_SUPPORT,
            family=StructureFamily.HORIZONTAL,
            side=ZoneSide.SUPPORT,
            timeframe_minutes=self.higher_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=bar.low,
            formed_index=len(self.decision_bars) - 1,
            formed_time_ns=bar.ts_close_ns,
            observed_time_ns=bar.ts_close_ns,
            formation_indices=tuple(item.index for item in pattern),
            strength_ratio=leg1 / leg2,
            source_structure_id=f"{self.symbol}:15m:TERMINAL_WEDGE:{pattern_id}",
            source_pivot_span=self.PIVOT_SPAN,
        )
        return pattern_id, zone, invalidation

    def _rising_wedge(self, bar: Candle) -> tuple[str, StructureZone, float] | None:
        pattern = self._latest_pattern(self._pivots_before(bar.ts_close_ns), ("LOW", "HIGH", "LOW", "HIGH", "LOW"))
        if pattern is None:
            return None
        low1, high1, low2, high2, low3 = pattern
        if not (low1.price < low2.price < low3.price and high1.price < high2.price):
            return None
        leg1 = high1.price - low1.price
        leg2 = high2.price - low2.price
        current_leg = bar.high - low3.price
        if not (leg1 > leg2 > 0.0 and 0.0 < current_leg <= leg2 and bar.high > high2.price):
            return None
        lower_slope = self._slope(low2, low3)
        upper_slope = self._slope(high1, high2)
        if not (lower_slope > upper_slope > 0.0):
            return None
        lower_now = self._line_at(low2, low3, bar.ts_close_ns)
        upper_now = self._line_at(high1, high2, bar.ts_close_ns)
        if not (upper_now > lower_now and bar.high >= upper_now):
            return None

        invalidation = low3.price + leg2
        if bar.high >= invalidation:
            return None
        pattern_id = "RISING:" + "|".join(item.pivot_id for item in pattern)
        body_high = max(bar.open, bar.close)
        upper = bar.high
        lower = min(body_high, upper - self.tick_size)
        zone = StructureZone(
            zone_id=f"{self.symbol}:15m:TERMINAL_WEDGE:RESISTANCE:{pattern_id}:SNAP:{bar.ts_close_ns}",
            kind=ObjectKind.HORIZONTAL_RESISTANCE,
            family=StructureFamily.HORIZONTAL,
            side=ZoneSide.RESISTANCE,
            timeframe_minutes=self.higher_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=bar.high,
            formed_index=len(self.decision_bars) - 1,
            formed_time_ns=bar.ts_close_ns,
            observed_time_ns=bar.ts_close_ns,
            formation_indices=tuple(item.index for item in pattern),
            strength_ratio=leg1 / leg2,
            source_structure_id=f"{self.symbol}:15m:TERMINAL_WEDGE:{pattern_id}",
            source_pivot_span=self.PIVOT_SPAN,
        )
        return pattern_id, zone, invalidation

    def _arm_wedge(self, bar: Candle, index: int, candidate: tuple[str, StructureZone, float]) -> None:
        pattern_id, context, invalidation = candidate
        if pattern_id in self._armed_wedge_ids:
            return
        self._armed_wedge_ids.add(pattern_id)
        setup = self._create_setup(
            path=ScenarioPath.REJECTION,
            context=context,
            members=(context,),
            bar=bar,
            decision_index=index,
            state=SetupState.WAITING_DISPLACEMENT,
        )
        if setup is None:
            self._winc("terminal_wedge_without_trade_geometry")
            return
        setup.interaction_extreme = (
            invalidation + self.tick_size
            if setup.side is Side.LONG
            else invalidation - self.tick_size
        )
        self._winc("terminal_wedge_setup_created")
        self._trace(
            "terminal_wedge_setup_created",
            bar.ts_close_ns,
            setup,
            pattern_id=pattern_id,
            pattern_invalidation=invalidation,
            terminal_low=bar.low,
            terminal_high=bar.high,
            rule_provenance=(WEDGE_SHRINKING_LEG_RULE, WEDGE_CONVERGENCE_RULE),
        )

    def _discover_interactions(self, bar: Candle, previous: Candle, index: int) -> None:
        del previous
        falling = self._falling_wedge(bar)
        if falling is not None:
            self._arm_wedge(bar, index, falling)
        rising = self._rising_wedge(bar)
        if rising is not None:
            self._arm_wedge(bar, index, rising)

    @property
    def wedge_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._wedge_counts.items())),
            "armed_patterns": len(self._armed_wedge_ids),
            "rules": (WEDGE_SHRINKING_LEG_RULE, WEDGE_CONVERGENCE_RULE),
        }


class EasyChartRE1WedgeBundle(EasyChartRE1HumanPolicyBundle):
    """Human-entry policy plus an independent terminal-wedge reversal family."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.wedge = TerminalWedgeScenarioEngine(
            symbol,
            tick_size,
            scale_name="TERMINAL_WEDGE",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["wedge"] = 0
        self._wedge_bundle_counts: dict[str, int] = {}

    def _wbinc(self, key: str) -> None:
        self._wedge_bundle_counts[key] = self._wedge_bundle_counts.get(key, 0) + 1

    @property
    def setups(self):
        return super().setups + self.wedge.setups

    @property
    def plans(self):
        return super().plans + self.wedge.plans

    def _route_wedge(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(raw, key=lambda item: (item.interaction_time_ns, item.observed_time_ns, item.plan_id)):
            if self._duplicate_episode(plan):
                self._wbinc("terminal_wedge_overlapped_existing_family")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._wbinc("terminal_wedge_plan_allowed")
            self._bundle_trace.append(
                {
                    "scenario_kind": "terminal_wedge_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": (WEDGE_SHRINKING_LEG_RULE, WEDGE_CONVERGENCE_RULE),
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        wedge_plans: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            raw = self.wedge.on_bar(timeframe_minutes, bar)
            self._sync_audit("wedge", self.wedge)
            wedge_plans = self._route_wedge(raw)
        routed = EasyChartRE1HumanPolicyBundle.on_bar(self, timeframe_minutes, bar)
        return sorted(
            wedge_plans + routed,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        return super().drain_trace() + self.wedge.drain_trace()

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.wedge.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["terminal_wedge_family"] = {
            "router_counts": dict(sorted(self._wedge_bundle_counts.items())),
            "engine": self.wedge.diagnostics,
            "geometry": self.wedge.natural_geometry_diagnostics,
            "entry": self.wedge.immediate_entry_diagnostics,
            "wedge": self.wedge.wedge_diagnostics,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1WedgeBundle
