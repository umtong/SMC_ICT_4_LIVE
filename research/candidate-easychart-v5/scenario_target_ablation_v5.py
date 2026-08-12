"""Nearest causal objective policy without changing the trading contract.

The source names prior highs/lows and opposing structures as objectives but
does not require an objective pivot to share the entry context's machine span.
The integrated engine now searches the complete 15m/5m/1m objective ladder.
This compatibility module keeps the meaningful-structure context book and that
ladder pointed at the same causal 15m state.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import V5TradePlan
from domain import Side
from scenario_bundle_v5 import AuditFrame, ResearchScenarioBundleV5
from scenario_engine_v5 import StructureScenarioEngine
from structure_admission_v5 import SourceFaithfulStructureBook


NEAREST_ANY_PIVOT_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "NEAREST_ANY_CONFIRMED_PREEXISTING_OPPOSITE_PIVOT_IS_OBJECTIVE"
)
if NEAREST_ANY_PIVOT_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (NEAREST_ANY_PIVOT_RULE,)


class NearestAnyPivotStructureBook(LifecycleAwareStructureBook):
    """Legacy compatibility book; the integrated ladder owns target choice."""

    def target_for(
        self,
        side: Side,
        *,
        interaction_time_ns: int,
        source_span: int,
        current_high: float,
        current_low: float,
    ):
        del source_span
        wanted = "HIGH" if side is Side.LONG else "LOW"
        candidates = [
            pivot
            for pivot in self.pivots
            if pivot.side == wanted
            and pivot.observed_time_ns < interaction_time_ns
            and not (
                pivot.consumed_time_ns is not None
                and pivot.consumed_time_ns < interaction_time_ns
            )
            and (
                (side is Side.LONG and pivot.price > current_high)
                or (side is Side.SHORT and pivot.price < current_low)
            )
        ]
        if not candidates:
            return None
        pivot = (
            min(candidates, key=lambda item: (item.price, -item.span, item.pivot_id))
            if side is Side.LONG
            else max(candidates, key=lambda item: (item.price, item.span, item.pivot_id))
        )
        self._inc("nearest_any_pivot_target_selected")
        return self._horizontal_snapshot(pivot, interaction_time_ns), pivot.price


class NearestAnyTargetScenarioEngine(StructureScenarioEngine):
    """One source-faithful context book and one synchronized objective ladder."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = SourceFaithfulStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )
        # StructureScenarioEngine creates the cross-timeframe ladder before a
        # subclass can select its concrete context book.  Synchronize the
        # primary reference once, before any bar is processed.
        self.objectives.primary = self.structure
        self.objectives.books = (
            self.structure,
            self.objectives.decision,
            self.objectives.trigger,
        )


class NearestAnyTargetResearchScenarioBundleV5(ResearchScenarioBundleV5):
    """Both decision scales using the complete nearest-objective policy."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.macro = NearestAnyTargetScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO",
            higher_minutes=60,
            decision_minutes=15,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = NearestAnyTargetScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = {60: AuditFrame(60), 15: AuditFrame(15), 5: AuditFrame(5), 1: AuditFrame(1)}
        self._claimed_episodes: list[tuple[Side, int, int, float, float]] = []
        self._bundle_trace: list[dict[str, Any]] = []
        self._audit_offsets = {"macro": 0, "micro": 0}

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["target_policy"] = {
            "name": "NEAREST_CONFIRMED_PREEXISTING_OPPOSITE_OBJECTIVE_ACROSS_STACK",
            "rule_provenance": NEAREST_ANY_PIVOT_RULE,
        }
        return output


BUNDLE_BY_TARGET_POLICY = {
    "scale_matched": ResearchScenarioBundleV5,
    "nearest_any": NearestAnyTargetResearchScenarioBundleV5,
}
