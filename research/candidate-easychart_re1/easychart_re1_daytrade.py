"""First-obstacle day-trading objectives for response-confirmed RE1 families.

The entry work in RE1 now separates real auction mechanisms and confirms the
first post-retest response.  The remaining mismatch with the supplied live
trades is objective choice: a 15-minute channel or old pivot can be structurally
valid yet sit far beyond the first resistance/support that a day trader must
actually negotiate.

This module preserves one full position and one predeclared target, but selects
the first still-live opposing structure among:

* the inherited scenario objective (opposite channel edge, extension or prior
  structure);
* the nearest opposing confirmed 15-minute pivot;
* the nearest opposing confirmed 5-minute pivot.

No synthetic R target is created.  If the first obstacle does not leave at least
the existing minimum gross RR, the setup is rejected rather than substituting a
farther level.  Thus target distance follows the observable auction, not a
10R/20R aspiration.  All direction, footprint, response, stop, risk, cost and
execution rules remain unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, StructureZone
from domain import Candle, Side
from easychart_re1_confirmed import (
    ConfirmedRepeatedDefenseScenarioEngine,
    ConfirmedSelectiveScenarioEngine,
)
from easychart_re1_major_swing import (
    EasyChartRE1MajorSwingBundle,
    MajorSwingLiquidityScenarioEngine,
)
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


DAYTRADE_FIRST_OBSTACLE_CONFIRMED_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "RESPONSE_CONFIRMED_FULL_POSITION_TARGETS_FIRST_LIVE_OPPOSING_5M_OR_15M_STRUCTURE_BEFORE_FARTHER_OBJECTIVE"
)
if DAYTRADE_FIRST_OBSTACLE_CONFIRMED_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (DAYTRADE_FIRST_OBSTACLE_CONFIRMED_RULE,)


TargetChoice = tuple[StructureZone, float, str | None, float | None]


class DaytradeObjectiveMixin:
    """Select a causal first obstacle without changing the entry state machine."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.decision_objectives = NearestAnyPivotStructureBook(
            self.symbol,
            self.decision_minutes,
            self.tick_size,
        )
        self._daytrade_objective_counts: dict[str, int] = {}

    def _objective_inc(self, key: str) -> None:
        self._daytrade_objective_counts[key] = (
            self._daytrade_objective_counts.get(key, 0) + 1
        )

    @staticmethod
    def _choice_from_pivot(
        target: tuple[StructureZone, float],
    ) -> TargetChoice:
        zone, price = target
        return zone, price, None, None

    def _append_unique(
        self,
        candidates: list[tuple[str, TargetChoice]],
        source: str,
        choice: TargetChoice | None,
    ) -> None:
        if choice is None:
            return
        zone, price, _, _ = choice
        for _, existing in candidates:
            old_zone, old_price, _, _ = existing
            if old_zone.zone_id == zone.zone_id:
                return
            if abs(old_price - price) <= self.tick_size * 0.5:
                return
        candidates.append((source, choice))

    @staticmethod
    def _first_candidate(
        side: Side,
        candidates: list[tuple[str, TargetChoice]],
    ) -> tuple[str, TargetChoice]:
        # Existing objective wins an exact-price tie; otherwise the first price
        # encountered in the trade direction is authoritative.
        if side is Side.LONG:
            return min(
                enumerate(candidates),
                key=lambda item: (item[1][1][1], item[0]),
            )[1]
        return max(
            enumerate(candidates),
            key=lambda item: (item[1][1][1], -item[0]),
        )[1]

    def _select_target(
        self,
        context: StructureZone,
        side: Side,
        path: ScenarioPath,
        bar: Candle,
    ) -> TargetChoice | None:
        inherited = super()._select_target(context, side, path, bar)
        candidates: list[tuple[str, TargetChoice]] = []
        self._append_unique(candidates, "INHERITED_OBJECTIVE", inherited)

        higher = self.structure.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        self._append_unique(
            candidates,
            "15M_OPPOSING_STRUCTURE",
            None if higher is None else self._choice_from_pivot(higher),
        )

        decision = self.decision_objectives.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        self._append_unique(
            candidates,
            "5M_OPPOSING_STRUCTURE",
            None if decision is None else self._choice_from_pivot(decision),
        )

        if not candidates:
            return None
        source, selected = self._first_candidate(side, candidates)
        zone, price, _, _ = selected
        self._objective_inc(f"selected_{source.lower()}")
        if source != "INHERITED_OBJECTIVE":
            self._objective_inc("nearer_obstacle_replaced_farther_objective")
        self._trace(
            "response_confirmed_daytrade_target_selected",
            bar.ts_close_ns,
            side=side.name,
            path=path.value,
            context_zone_id=context.zone_id,
            selected_source=source,
            selected_zone_id=zone.zone_id,
            selected_timeframe_minutes=zone.timeframe_minutes,
            selected_price=price,
            candidates=[
                {
                    "source": candidate_source,
                    "zone_id": candidate[0].zone_id,
                    "timeframe_minutes": candidate[0].timeframe_minutes,
                    "price": candidate[1],
                }
                for candidate_source, candidate in candidates
            ],
            rule_provenance=DAYTRADE_FIRST_OBSTACLE_CONFIRMED_RULE,
        )
        return selected

    def _target_is_spent(self, setup: ScenarioSetup, bar: Candle) -> bool:
        target = setup.target_zone
        if target is not None and target.timeframe_minutes == self.decision_minutes:
            if setup.target_price is None:
                return True
            touched = (
                bar.high >= setup.target_price
                if setup.side is Side.LONG
                else bar.low <= setup.target_price
            )
            return touched and bar.ts_close_ns > setup.interaction_time_ns
        return super()._target_is_spent(setup, bar)

    def on_bar(self, timeframe_minutes: int, bar: Candle):  # type: ignore[no-untyped-def]
        if timeframe_minutes != self.decision_minutes:
            return super().on_bar(timeframe_minutes, bar)
        self.decision_objectives.on_bar(bar)
        plans = super().on_bar(timeframe_minutes, bar)
        self.decision_objectives.observe_price(bar)
        return plans

    @property
    def daytrade_objective_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._daytrade_objective_counts.items())),
            "decision_structure": dict(self.decision_objectives.diagnostics),
            "rule_provenance": DAYTRADE_FIRST_OBSTACLE_CONFIRMED_RULE,
        }


class DaytradeConfirmedSelectiveScenarioEngine(
    DaytradeObjectiveMixin,
    ConfirmedSelectiveScenarioEngine,
):
    """Diagonal response-confirmed engine with the first-obstacle objective."""


class DaytradeConfirmedRepeatedDefenseScenarioEngine(
    DaytradeObjectiveMixin,
    ConfirmedRepeatedDefenseScenarioEngine,
):
    """Repeated-defense sweep engine with the first-obstacle objective."""


class DaytradeMajorSwingLiquidityScenarioEngine(
    DaytradeObjectiveMixin,
    MajorSwingLiquidityScenarioEngine,
):
    """Single-major-swing engine with the first-obstacle objective."""


class EasyChartRE1DaytradeBundle(EasyChartRE1MajorSwingBundle):
    """All demonstrated response-confirmed families with natural objectives."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = DaytradeConfirmedSelectiveScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal = DaytradeConfirmedRepeatedDefenseScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.major_swing = DaytradeMajorSwingLiquidityScenarioEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0
        self._audit_offsets["horizontal"] = 0
        self._audit_offsets["major_swing"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["response_confirmed_daytrade_objectives"] = {
            "micro": self.micro.daytrade_objective_diagnostics,
            "horizontal": self.horizontal.daytrade_objective_diagnostics,
            "major_swing": self.major_swing.daytrade_objective_diagnostics,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DaytradeBundle
