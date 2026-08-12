"""Day-trading objective policy for EasyChart RE1.

The source material gives the channel opposite edge as the textbook rotation
objective, but the supplied live day-trading case does something more specific:
a large descending channel supplies the long context while the full position is
closed at the nearer low-timeframe resistance made by the preceding downswing.
The trader explicitly says that the larger pattern leaves more room, yet the
account is a day-trading account and therefore exits where resistance can first
appear.

The project contract removes partial exits.  The least distorting deterministic
translation is therefore one full predeclared target at the first still-live
opposing structure visible on the 5-minute decision chart or the 15-minute
context chart, before a farther channel edge/extension.  A closer structure
below 1R does not cause an arbitrary target substitution: the existing gross-RR
rule simply rejects the plan.

This module changes target selection only.  Direction routing, first-touch HTF
footprints, setup state, entry, stop, risk, costs and NautilusTrader execution
remain unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, StructureZone
from diagonal_core_v20 import DiagonalCoreScenarioEngine
from domain import Candle, Side
from easychart_re1_fresh import EasyChartRE1FreshBundle
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


DAYTRADE_FIRST_OBSTACLE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ONE_FULL_DAYTRADE_TARGET_IS_FIRST_LIVE_OPPOSING_5M_OR_15M_STRUCTURE_BEFORE_CHANNEL_OBJECTIVE"
)
if DAYTRADE_FIRST_OBSTACLE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (DAYTRADE_FIRST_OBSTACLE_RULE,)


TargetChoice = tuple[StructureZone, float, str | None, float | None]


class DaytradeObjectiveScenarioEngine(DiagonalCoreScenarioEngine):
    """Diagonal execution core with a causal 5m/15m first-obstacle target."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.decision_objectives = NearestAnyPivotStructureBook(
            self.symbol,
            self.decision_minutes,
            self.tick_size,
        )
        self._objective_counts: dict[str, int] = {}

    def _objective_inc(self, key: str) -> None:
        self._objective_counts[key] = self._objective_counts.get(key, 0) + 1

    @staticmethod
    def _first_candidate(
        side: Side,
        candidates: list[tuple[str, TargetChoice]],
    ) -> tuple[str, TargetChoice]:
        # ``candidates`` is appended with the inherited objective first.  The
        # list index therefore preserves the existing policy for an exact
        # price tie while price distance decides every non-tie.
        if side is Side.LONG:
            return min(
                enumerate(candidates),
                key=lambda item: (item[1][1][1], item[0]),
            )[1]
        return max(
            enumerate(candidates),
            key=lambda item: (item[1][1][1], -item[0]),
        )[1]

    @staticmethod
    def _choice_from_pivot(target: tuple[StructureZone, float]) -> TargetChoice:
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
            existing_zone, existing_price, _, _ = existing
            if existing_zone.zone_id == zone.zone_id or (
                abs(existing_price - price) <= self.tick_size * 0.5
            ):
                return
        candidates.append((source, choice))

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

        higher_pivot = self.structure.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        self._append_unique(
            candidates,
            "15M_OPPOSING_PIVOT",
            None if higher_pivot is None else self._choice_from_pivot(higher_pivot),
        )

        decision_pivot = self.decision_objectives.target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=context.source_pivot_span,
            current_high=bar.high,
            current_low=bar.low,
        )
        self._append_unique(
            candidates,
            "5M_OPPOSING_PIVOT",
            None if decision_pivot is None else self._choice_from_pivot(decision_pivot),
        )

        if not candidates:
            return None
        source, selected = self._first_candidate(side, candidates)
        selected_zone, selected_price, _, _ = selected
        self._objective_inc(f"target_selected_{source.lower()}")
        if source != "INHERITED_OBJECTIVE":
            self._objective_inc("nearer_structure_replaced_farther_objective")
        self._trace(
            "daytrade_first_obstacle_target_selected",
            bar.ts_close_ns,
            side=side.name,
            path=path.value,
            context_zone_id=context.zone_id,
            selected_source=source,
            selected_zone_id=selected_zone.zone_id,
            selected_timeframe_minutes=selected_zone.timeframe_minutes,
            selected_price=selected_price,
            candidates=[
                {
                    "source": candidate_source,
                    "zone_id": candidate[0].zone_id,
                    "timeframe_minutes": candidate[0].timeframe_minutes,
                    "price": candidate[1],
                }
                for candidate_source, candidate in candidates
            ],
            rule_provenance=DAYTRADE_FIRST_OBSTACLE_RULE,
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
        # The current completed decision bar can create one setup before it
        # consumes touched objective pivots for later unrelated interactions.
        self.decision_objectives.observe_price(bar)
        return plans

    @property
    def objective_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._objective_counts.items())),
            "decision_objective_structure": dict(self.decision_objectives.diagnostics),
            "rule_provenance": DAYTRADE_FIRST_OBSTACLE_RULE,
        }


class EasyChartRE1ObjectiveBundle(EasyChartRE1FreshBundle):
    """Canonical RE1 bundle with first-touch context and daytrade objectives."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = DaytradeObjectiveScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["daytrade_objective_policy"] = {
            "name": "FIRST_LIVE_OPPOSING_5M_OR_15M_STRUCTURE_BEFORE_CHANNEL_OBJECTIVE",
            **self.micro.objective_diagnostics,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ObjectiveBundle
