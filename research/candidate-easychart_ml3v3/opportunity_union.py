"""Union of independent causal EasyChart opportunity hypotheses for ML3v3.

The preceding complete policy is not treated as a benchmark or a gate. Each
existing bundle is reused only as a causal hypothesis generator. Plans keep
immutable entry, invalidation and objective geometry. Exact duplicate geometry
is collapsed; genuinely different geometry from the same account decision
bucket remains available to the learned account router.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle
from candidate_bundle_ml1 import (
    EasyChartML1CandidateBundle,
    ML1MatureDiagonalResponseFamily,
)
from easychart_re1_confluence_flip_direct import EasyChartRE1DirectConfluenceBundle
from easychart_re1_direct_sweep_ob import EasyChartRE1DirectSweepOBBundle
from easychart_re1_flow_routed import EasyChartRE1FlowRoutedBundle
from easychart_re1_human_policy import EasyChartRE1HumanPolicyBundle
from easychart_re1_macro_trend_pullback import EasyChartRE1MacroTrendOpportunityBundle
from easychart_re1_skilled_integrated import EasyChartRE1SkilledIntegratedBundle


OPPORTUNITY_UNION_RULE = (
    "RESEARCH_HYPOTHESIS:INDEPENDENT_CAUSAL_AUCTION_HYPOTHESES_EXPOSE_FROZEN_"
    "PLANS_TO_ONE_LEARNED_ACCOUNT_ROUTER_WITHOUT_TREATING_ANY_PRECEDING_POLICY_"
    "AS_THE_STANDARD"
)
EXACT_GEOMETRY_DEDUPLICATION_RULE = (
    "IMPLEMENTATION_VALIDITY:IDENTICAL_SYMBOL_SIDE_OBSERVATION_ENTRY_STOP_TARGET_"
    "GEOMETRY_IS_ONE_CANDIDATE_WHILE_DISTINCT_GEOMETRY_REMAINS_A_LEARNABLE_VARIANT"
)
ACCOUNT_BUCKET_EPISODE_RULE = (
    "IMPLEMENTATION_VALIDITY:SAME_SYMBOL_SIDE_AND_CAUSAL_INTERACTION_TIME_SHARE_"
    "ONE_ACCOUNT_OPPORTUNITY_BUCKET_FOR_SAMPLE_WEIGHTING_AND_AUDIT"
)
for _rule in (
    OPPORTUNITY_UNION_RULE,
    EXACT_GEOMETRY_DEDUPLICATION_RULE,
    ACCOUNT_BUCKET_EPISODE_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class EasyChartML3V3MacroOpportunityBundle(EasyChartRE1MacroTrendOpportunityBundle):
    """Independent macro hypothesis with explicit 15m/5m/1m diagonal inputs."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        # The aggregate receives 60m context bars, but the mature diagonal
        # response engine is explicitly a 15m/5m/1m mechanism.  Replace only
        # that child with the audited timeframe adapter rather than swallowing
        # an unsupported-timeframe error or discarding the macro hypothesis.
        self.mature_diagonal_acceptance = ML1MatureDiagonalResponseFamily(
            symbol,
            tick_size,
            minimum_gross_rr,
        )


_GENERATORS: tuple[tuple[str, type[Any]], ...] = (
    ("complete", EasyChartML1CandidateBundle),
    ("human", EasyChartRE1HumanPolicyBundle),
    ("skilled", EasyChartRE1SkilledIntegratedBundle),
    ("flow", EasyChartRE1FlowRoutedBundle),
    ("direct_sweep", EasyChartRE1DirectSweepOBBundle),
    ("confluence", EasyChartRE1DirectConfluenceBundle),
    ("macro", EasyChartML3V3MacroOpportunityBundle),
)


class EasyChartML3V3OpportunityUnion:
    """Expose complete, independently motivated plans to a single ML router."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = float(tick_size)
        self.minimum_gross_rr = float(minimum_gross_rr)
        self.generators: dict[str, Any] = {
            owner: cls(symbol, tick_size, minimum_gross_rr)
            for owner, cls in _GENERATORS
        }
        self.detectors = getattr(self.generators["complete"], "detectors", {})
        self._plans: list[V5TradePlan] = []
        self._trace: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}
        self._plan_maps: dict[str, dict[str, str]] = {
            owner: {} for owner in self.generators
        }
        self._seen_plan_ids: set[str] = set()
        self._seen_geometry: dict[tuple[Any, ...], str] = {}

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    @property
    def _macro_side(self) -> Any | None:
        """Expose one causal broad-direction state as model evidence only."""
        return getattr(self.generators["complete"], "_macro_side", None)

    def set_market_factor_state(self, state: Any | None) -> None:
        """Propagate causal common state as evidence, never as a union-level gate."""
        for generator in self.generators.values():
            setter = getattr(generator, "set_market_factor_state", None)
            if setter is not None:
                setter(state)

    def _geometry_key(self, plan: V5TradePlan) -> tuple[Any, ...]:
        tick = max(abs(self.tick_size), 1e-12)
        return (
            plan.symbol,
            plan.side.name,
            int(plan.observed_time_ns),
            int(round(float(plan.entry) / tick)),
            int(round(float(plan.stop) / tick)),
            int(round(float(plan.target) / tick)),
        )

    def _namespace(self, owner: str, plan: V5TradePlan) -> V5TradePlan | None:
        mapping = self._plan_maps[owner]
        if plan.plan_id in mapping:
            raise RuntimeError(
                f"{owner} emitted duplicate raw plan id {plan.plan_id!r}"
            )
        namespaced = f"ml3v3-{owner}-{plan.plan_id}"
        if namespaced in self._seen_plan_ids:
            raise RuntimeError(f"union plan id collision {namespaced!r}")
        mapping[plan.plan_id] = namespaced
        self._seen_plan_ids.add(namespaced)

        geometry = self._geometry_key(plan)
        previous = self._seen_geometry.get(geometry)
        if previous is not None:
            self._inc("exact_geometry_duplicate_collapsed")
            self._trace.append(
                {
                    "scenario_kind": "ml3v3_exact_geometry_duplicate_collapsed",
                    "event_time_ns": int(plan.observed_time_ns),
                    "symbol": plan.symbol,
                    "owner": owner,
                    "suppressed_plan_id": namespaced,
                    "owner_plan_id": previous,
                    "entry": float(plan.entry),
                    "stop": float(plan.stop),
                    "target": float(plan.target),
                    "rule_provenance": EXACT_GEOMETRY_DEDUPLICATION_RULE,
                }
            )
            return None
        self._seen_geometry[geometry] = namespaced

        bucket_id = (
            f"ML3V3_BUCKET:{plan.symbol}:{plan.side.name}:"
            f"{int(plan.interaction_time_ns)}"
        )
        output = replace(
            plan,
            plan_id=namespaced,
            causal_event_id=bucket_id,
            family=f"{owner.upper()}::{plan.family}",
            source_rule_count=int(plan.source_rule_count) + 3,
            rule_provenance=tuple(plan.rule_provenance)
            + (
                f"RESEARCH_HYPOTHESIS:ML3V3_GENERATOR_OWNER={owner.upper()}",
                OPPORTUNITY_UNION_RULE,
                ACCOUNT_BUCKET_EPISODE_RULE,
            ),
        )
        self._inc(f"owner_{owner}_candidate")
        return output

    def _rewrite_trace(
        self,
        owner: str,
        rows: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        mapping = self._plan_maps[owner]
        output: list[dict[str, Any]] = []
        for source in rows:
            row = dict(source)
            for key in ("plan_id", "suppressed_plan_id", "owner_plan_id"):
                value = row.get(key)
                if isinstance(value, str) and value in mapping:
                    row[key] = mapping[value]
            row.setdefault("ml3v3_generator_owner", owner)
            output.append(row)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        emitted: list[V5TradePlan] = []
        for owner, generator in self.generators.items():
            raw = generator.on_bar(timeframe_minutes, bar)
            for plan in raw:
                namespaced = self._namespace(owner, plan)
                if namespaced is not None:
                    emitted.append(namespaced)
        emitted.sort(
            key=lambda plan: (
                int(plan.interaction_time_ns),
                -int(plan.higher_timeframe_minutes),
                int(plan.observed_time_ns),
                plan.family,
                plan.plan_id,
            )
        )
        self._plans.extend(emitted)
        return emitted

    def drain_trace(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for owner, generator in self.generators.items():
            output.extend(self._rewrite_trace(owner, generator.drain_trace()))
        output.extend(self._trace)
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        for generator in self.generators.values():
            finder = getattr(generator, "find_zone", None)
            if finder is None:
                continue
            result = finder(zone_id)
            if result is not None:
                return result
        return None

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self) -> list[Any]:
        output: list[Any] = []
        for generator in self.generators.values():
            output.extend(list(getattr(generator, "setups", ())))
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "ml3v3_opportunity_union": {
                "owners": tuple(self.generators),
                "counts": dict(sorted(self._counts.items())),
                "rules": (
                    OPPORTUNITY_UNION_RULE,
                    EXACT_GEOMETRY_DEDUPLICATION_RULE,
                    ACCOUNT_BUCKET_EPISODE_RULE,
                ),
            },
            "generators": {
                owner: getattr(generator, "diagnostics", {})
                for owner, generator in self.generators.items()
            },
        }


MultiScaleScenarioBundle = EasyChartML3V3OpportunityUnion
