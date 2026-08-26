"""Mechanism-routed EasyChart RE1 day-trading policy.

This module joins complete auction decisions rather than accumulating filters:

* rejection opportunities are actual sweep/reclaim rejection episodes; visual
  OB/FVG returns remain direct, while a flow-only substitute must first complete
  the five-minute control-transfer event;
* accepted structure transfers require the first completed response and use the
  return extreme plus the first transfer-local opposing swing for geometry;
* an unbroken decision-OB bounce is not relabeled as a reversal.  Continuation
  responsibility belongs to the dedicated nested-initiative pullback family
  composed above this bundle.

The policies see the same completed bars, but only the mechanism each owns is
executable.  A simultaneous overlap is one causal opportunity: accepted control
has priority over a rejection interpretation.  There is no PnL-dependent
routing, fitted score, trade cap, partial exit, stop movement or target movement
after entry.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle
from easychart_re1_acceptance_geometry import (
    MultiScaleScenarioBundle as AcceptanceGeometryBundle,
)
from easychart_re1_controlled_significant import (
    MultiScaleScenarioBundle as ControlledSignificantResponseBundle,
)


MECHANISM_ROUTED_SKILLED_POLICY_RULE = (
    "RESEARCH_HYPOTHESIS:CONTROL_TRANSFER_CONFIRMED_SIGNIFICANT_OBJECTIVE_"
    "REVERSALS_AND_RESPONSE_CONFIRMED_TRANSFER_LOCAL_GEOMETRY_ACCEPTANCE_"
    "OWN_DISTINCT_AUCTION_MECHANISMS"
)
SIMULTANEOUS_EPISODE_OWNERSHIP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_COMPLETED_ACCEPTANCE_RESPONSE_OWNS_A_"
    "SIMULTANEOUS_OVERLAPPING_BOUNDARY_EPISODE_OVER_A_REJECTION_INTERPRETATION"
)
UNBROKEN_OB_BOUNCE_RESPONSIBILITY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:AN_UNBROKEN_ORDER_BLOCK_BOUNCE_IS_A_"
    "CONTINUATION_MECHANISM_AND_CANNOT_ENTER_THROUGH_THE_REJECTION_OWNER"
)
PLAN_ID_NAMESPACE_RULE = (
    "IMPLEMENTATION_VALIDITY:EACH_MECHANISM_OWNER_NAMESPACES_PLAN_AND_CAUSAL_"
    "EVENT_IDENTITIES_BEFORE_GLOBAL_ROUTING_SO_INDEPENDENT_ENGINE_COUNTERS_"
    "CANNOT_CORRUPT_AUDIT_OR_COUNTERFACTUAL_JOIN_KEYS"
)
for _rule in (
    MECHANISM_ROUTED_SKILLED_POLICY_RULE,
    SIMULTANEOUS_EPISODE_OWNERSHIP_RULE,
    UNBROKEN_OB_BOUNCE_RESPONSIBILITY_RULE,
    PLAN_ID_NAMESPACE_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class EasyChartRE1SkilledIntegratedBundle:
    """One executable plan stream with explicit mechanism responsibility."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.reversal = ControlledSignificantResponseBundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.acceptance = AcceptanceGeometryBundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.detectors = self.reversal.detectors
        self._plans: list[V5TradePlan] = []
        self._counts: dict[str, int] = {}
        self._trace: list[dict[str, Any]] = []
        self._plan_id_maps: dict[str, dict[str, str]] = {
            "reversal": {},
            "acceptance": {},
        }
        self._seen_plan_ids: set[str] = set()

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _namespace_plan(self, owner: str, plan: V5TradePlan) -> V5TradePlan:
        mapping = self._plan_id_maps[owner]
        existing = mapping.get(plan.plan_id)
        if existing is not None:
            raise RuntimeError(
                f"{owner} emitted duplicate raw plan id {plan.plan_id!r}; "
                "plan identity must be unique within one mechanism owner"
            )
        namespaced = f"skilled-{owner}-{plan.plan_id}"
        if namespaced in self._seen_plan_ids:
            raise RuntimeError(f"global skilled plan id collision {namespaced!r}")
        mapping[plan.plan_id] = namespaced
        self._seen_plan_ids.add(namespaced)
        self._inc(f"{owner}_plan_id_namespaced")
        return replace(
            plan,
            plan_id=namespaced,
            causal_event_id=f"SKILLED_{owner.upper()}:{plan.causal_event_id}",
        )

    def _rewrite_owner_trace(
        self,
        owner: str,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        mapping = self._plan_id_maps[owner]
        for row in rows:
            for key in ("plan_id", "suppressed_plan_id", "owner_plan_id"):
                value = row.get(key)
                if isinstance(value, str) and value in mapping:
                    row[key] = mapping[value]
            causal = row.get("causal_event_id")
            if isinstance(causal, str) and not causal.startswith("SKILLED_"):
                row["causal_event_id"] = f"SKILLED_{owner.upper()}:{causal}"
        return rows

    def _same_completed_episode(
        self,
        left: V5TradePlan,
        right: V5TradePlan,
    ) -> bool:
        if left.symbol != right.symbol:
            return False
        if left.interaction_time_ns != right.interaction_time_ns:
            return False
        return (
            max(left.overlap_lower, right.overlap_lower)
            <= min(left.overlap_upper, right.overlap_upper) + self.tick_size
        )

    @staticmethod
    def _is_acceptance(plan: V5TradePlan) -> bool:
        return plan.scenario_path == ScenarioPath.ACCEPTANCE.value

    @staticmethod
    def _is_rejection(plan: V5TradePlan) -> bool:
        return plan.scenario_path == ScenarioPath.REJECTION.value

    def _route_current_bar(
        self,
        reversal_raw: list[V5TradePlan],
        acceptance_raw: list[V5TradePlan],
    ) -> list[V5TradePlan]:
        reversal_raw = [
            self._namespace_plan("reversal", plan) for plan in reversal_raw
        ]
        acceptance_raw = [
            self._namespace_plan("acceptance", plan) for plan in acceptance_raw
        ]
        rejection = [plan for plan in reversal_raw if self._is_rejection(plan)]
        deferred_bounce = [
            plan
            for plan in reversal_raw
            if plan.scenario_path == ScenarioPath.BOUNCE.value
        ]
        for plan in deferred_bounce:
            self._inc("unbroken_ob_bounce_deferred_to_continuation_owner")
            self._trace.append(
                {
                    "scenario_kind": "unbroken_ob_bounce_deferred",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "suppressed_plan_id": plan.plan_id,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "rule_provenance": UNBROKEN_OB_BOUNCE_RESPONSIBILITY_RULE,
                },
            )
        continuation = [plan for plan in acceptance_raw if self._is_acceptance(plan)]
        if rejection:
            self._inc("reversal_owned_plan")
        if continuation:
            self._inc("acceptance_owned_plan")

        selected: list[V5TradePlan] = sorted(
            continuation,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.symbol,
                item.plan_id,
            ),
        )
        for plan in sorted(
            rejection,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.symbol,
                item.plan_id,
            ),
        ):
            owner = next(
                (
                    existing
                    for existing in selected
                    if self._same_completed_episode(plan, existing)
                ),
                None,
            )
            if owner is not None:
                self._inc("simultaneous_rejection_suppressed_by_acceptance")
                self._trace.append(
                    {
                        "scenario_kind": "simultaneous_episode_owned_by_acceptance",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": owner.plan_id,
                        "interaction_time_ns": plan.interaction_time_ns,
                        "overlap_lower": max(plan.overlap_lower, owner.overlap_lower),
                        "overlap_upper": min(plan.overlap_upper, owner.overlap_upper),
                        "rule_provenance": SIMULTANEOUS_EPISODE_OWNERSHIP_RULE,
                    },
                )
                continue
            selected.append(plan)

        unique = {plan.plan_id: plan for plan in selected}
        output = sorted(
            unique.values(),
            key=lambda item: (
                item.interaction_time_ns,
                0 if self._is_acceptance(item) else 1,
                item.observed_time_ns,
                item.symbol,
                item.plan_id,
            ),
        )
        self._plans.extend(output)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        reversal_raw = self.reversal.on_bar(timeframe_minutes, bar)
        acceptance_raw = self.acceptance.on_bar(timeframe_minutes, bar)
        return self._route_current_bar(reversal_raw, acceptance_raw)

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            self._rewrite_owner_trace("reversal", self.reversal.drain_trace())
            + self._rewrite_owner_trace("acceptance", self.acceptance.drain_trace())
            + self._trace
        )
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self.reversal.find_zone(zone_id) or self.acceptance.find_zone(zone_id)

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self) -> list[Any]:
        return list(self.reversal.setups) + list(self.acceptance.setups)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "mechanism_routed_skilled_policy": {
                "counts": dict(sorted(self._counts.items())),
                "reversal_owner": "CONTROLLED_SIGNIFICANT_RESPONSE_REJECTION_ONLY",
                "acceptance_owner": "RESPONSE_CONFIRMED_TRANSFER_LOCAL_GEOMETRY",
                "bounce_owner": "NESTED_LOCAL_CONTINUATION",
                "plan_identity": "MECHANISM_OWNER_NAMESPACED",
                "rules": (
                    MECHANISM_ROUTED_SKILLED_POLICY_RULE,
                    SIMULTANEOUS_EPISODE_OWNERSHIP_RULE,
                    UNBROKEN_OB_BOUNCE_RESPONSIBILITY_RULE,
                    PLAN_ID_NAMESPACE_RULE,
                ),
            },
            "reversal": self.reversal.diagnostics,
            "acceptance": self.acceptance.diagnostics,
        }


MultiScaleScenarioBundle = EasyChartRE1SkilledIntegratedBundle
