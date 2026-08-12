"""Causal target-policy ablations for EasyChart v5.

The source repeatedly names prior highs/lows and opposing structures as
objectives, but it does not say that the objective pivot must use the same or a
larger machine pivot span than the entry context.  The current v5 policy adds
that scale restriction as a research hypothesis.  This module removes only
that one restriction while keeping the target pre-existing, unspent, opposite,
and nearest in price.  No outcome information, score, risk multiplier or
execution rule is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import ScenarioSetup, V5TradePlan
from domain import Side
from scenario_bundle_v5 import AuditFrame, ResearchScenarioBundleV5
from scenario_engine_v5 import StructureScenarioEngine


NEAREST_ANY_PIVOT_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "NEAREST_ANY_CONFIRMED_PREEXISTING_OPPOSITE_PIVOT_IS_OBJECTIVE"
)
if NEAREST_ANY_PIVOT_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (NEAREST_ANY_PIVOT_RULE,)


class NearestAnyPivotStructureBook(LifecycleAwareStructureBook):
    """Select the nearest eligible opposite pivot without a span gate."""

    def target_for(
        self,
        side: Side,
        *,
        interaction_time_ns: int,
        source_span: int,
        current_high: float,
        current_low: float,
    ):
        del source_span  # Deliberately ablated; retained in the public contract.
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
    """Unchanged state machine with the ablated objective book."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = NearestAnyPivotStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )


class NearestAnyTargetResearchScenarioBundleV5(ResearchScenarioBundleV5):
    """Both decision scales using the nearest-any-pivot target hypothesis."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        # Recreate the small bundle shell directly so no unused base engines
        # retain state or obscure which policy generated an audit record.
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
            "name": "NEAREST_ANY_CONFIRMED_PREEXISTING_OPPOSITE_PIVOT",
            "rule_provenance": NEAREST_ANY_PIVOT_RULE,
        }
        return output


BUNDLE_BY_TARGET_POLICY = {
    "scale_matched": ResearchScenarioBundleV5,
    "nearest_any": NearestAnyTargetResearchScenarioBundleV5,
}
