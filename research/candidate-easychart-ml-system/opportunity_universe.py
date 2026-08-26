"""Broad, mechanism-owned EasyChart opportunity universe.

The previous candidates failed in opposite ways: a broad flow bundle traded many
weak opportunities, while heavily translated deterministic policies produced too
few independent trades. This universe preserves distinct causal scenario owners
without pretending any one existing policy is the answer:

* FLOW: broad structure/liquidity interactions with observable initiative or
  absorption;
* SKILLED: completed sweep/reclaim reversal and accepted-control-transfer plans;
* PULLBACK: current-leg, latest-level first pullback continuation;
* H4: rejection of the immediately preceding completed four-hour auction
  extreme, followed by the first completed lower-timeframe response.

Every owner still fixes direction, entry, invalidation and objective before the
plan becomes executable. The ML router later selects among complete plans. No
owner is a fallback and no existing policy receives benchmark or priority status.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from contracts_v5 import V5TradePlan
from domain import Candle
from easychart_re1_efficient_pullback import EfficientPullbackEngine
from easychart_re1_flow import EasyChartRE1FlowBundle
from easychart_re1_h4_liquidity import H4LiquiditySweepEngine
from easychart_re1_skilled_integrated import EasyChartRE1SkilledIntegratedBundle

OPPORTUNITY_UNIVERSE_POLICY = (
    "ML_SYSTEM:FLOW_SKILLED_CURRENT_LEG_FIRST_PULLBACK_AND_COMPLETED_H4_AUCTION_"
    "OWN_DISTINCT_CAUSAL_OPPORTUNITIES_AND_EMIT_COMPLETE_PREENTRY_PLANS_WITHOUT_"
    "POLICY_PRIORITY"
)
IDENTITY_NAMESPACE_POLICY = (
    "IMPLEMENTATION_VALIDITY:PLAN_SETUP_CAUSAL_EVENT_AND_REFERENCED_ZONE_"
    "IDENTITIES_ARE_NAMESPACED_BY_MECHANISM_OWNER_BEFORE_AUDIT_AND_LABEL_JOIN"
)


class EasyChartMLOpportunityUniverse:
    """One namespaced plan stream from distinct auction mechanisms."""

    OWNER_ORDER = ("FLOW", "SKILLED", "PULLBACK", "H4")

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.owners: dict[str, Any] = {
            "FLOW": EasyChartRE1FlowBundle(symbol, tick_size, minimum_gross_rr),
            "SKILLED": EasyChartRE1SkilledIntegratedBundle(
                symbol,
                tick_size,
                minimum_gross_rr,
            ),
            # Use the independent mechanism engine, not the old integrated
            # policy wrapper. The wrapper recursively embeds unrelated owners
            # and expects a different routing stack; the engine itself is the
            # complete 15m -> 5m -> 1m first-pullback causal family.
            "PULLBACK": EfficientPullbackEngine(
                symbol,
                tick_size,
                minimum_gross_rr,
            ),
            # A skilled human scans a wider auction before using 15m/5m/1m for
            # timing. Four completed 60m bars provide that causal context
            # without adding a new feed or any future-dependent rolling range.
            "H4": H4LiquiditySweepEngine(
                symbol,
                tick_size,
                minimum_gross_rr,
            ),
        }
        self.detectors = getattr(self.owners["FLOW"], "detectors", None)
        self._plan_maps: dict[str, dict[str, str]] = {
            owner: {} for owner in self.OWNER_ORDER
        }
        self._zone_maps: dict[str, tuple[str, str]] = {}
        self._seen_plan_ids: set[str] = set()
        self._plans: list[V5TradePlan] = []
        self._counts: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    @staticmethod
    def _namespace(owner: str, value: str) -> str:
        prefix = f"{owner}|"
        return value if value.startswith(prefix) else f"{prefix}{value}"

    def _namespace_zone(self, owner: str, zone_id: str) -> str:
        namespaced = self._namespace(owner, zone_id)
        previous = self._zone_maps.get(namespaced)
        identity = (owner, zone_id)
        if previous is not None and previous != identity:
            raise RuntimeError(
                f"zone namespace collision {namespaced!r}: {previous!r} != {identity!r}",
            )
        self._zone_maps[namespaced] = identity
        return namespaced

    def _namespace_plan(self, owner: str, plan: V5TradePlan) -> V5TradePlan:
        mapping = self._plan_maps[owner]
        if plan.plan_id in mapping:
            raise RuntimeError(
                f"{owner} emitted duplicate raw plan id {plan.plan_id!r}",
            )
        plan_id = f"mlsys-{owner.lower()}-{plan.plan_id}"
        if plan_id in self._seen_plan_ids:
            raise RuntimeError(f"global ML-system plan id collision {plan_id!r}")
        mapping[plan.plan_id] = plan_id
        self._seen_plan_ids.add(plan_id)
        self._inc(f"{owner.lower()}_plan")
        return replace(
            plan,
            plan_id=plan_id,
            causal_event_id=self._namespace(owner, plan.causal_event_id),
            family=self._namespace(owner, plan.family),
            setup_id=self._namespace(owner, plan.setup_id),
            higher_zone_id=self._namespace_zone(owner, plan.higher_zone_id),
            lower_zone_id=self._namespace_zone(owner, plan.lower_zone_id),
            trigger_zone_id=self._namespace_zone(owner, plan.trigger_zone_id),
            target_zone_id=self._namespace_zone(owner, plan.target_zone_id),
        )

    def _rewrite_trace(self, owner: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        mapping = self._plan_maps[owner]
        for row in rows:
            row["mechanism_owner"] = owner
            for key in (
                "plan_id",
                "suppressed_plan_id",
                "owner_plan_id",
                "selected_plan_id",
            ):
                value = row.get(key)
                if isinstance(value, str) and value in mapping:
                    row[key] = mapping[value]
            setup = row.get("setup_id")
            if isinstance(setup, str):
                row["setup_id"] = self._namespace(owner, setup)
            causal = row.get("causal_event_id")
            if isinstance(causal, str):
                row["causal_event_id"] = self._namespace(owner, causal)
            family = row.get("family")
            if isinstance(family, str):
                row["family"] = self._namespace(owner, family)
            for key, value in list(row.items()):
                if key.endswith("zone_id") and isinstance(value, str):
                    row[key] = self._namespace_zone(owner, value)
            row["identity_namespace_policy"] = IDENTITY_NAMESPACE_POLICY
        return rows

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        emitted: list[V5TradePlan] = []
        for owner in self.OWNER_ORDER:
            raw = self.owners[owner].on_bar(timeframe_minutes, bar)
            emitted.extend(self._namespace_plan(owner, plan) for plan in raw)
        unique = {plan.plan_id: plan for plan in emitted}
        output = sorted(
            unique.values(),
            key=lambda plan: (
                plan.interaction_time_ns,
                plan.observed_time_ns,
                plan.symbol,
                plan.plan_id,
            ),
        )
        self._plans.extend(output)
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for owner in self.OWNER_ORDER:
            output.extend(
                self._rewrite_trace(owner, list(self.owners[owner].drain_trace())),
            )
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        mapped = self._zone_maps.get(zone_id)
        if mapped is not None:
            owner, raw_zone_id = mapped
            return self.owners[owner].find_zone(raw_zone_id)
        for owner in self.OWNER_ORDER:
            found = self.owners[owner].find_zone(zone_id)
            if found is not None:
                return found
        return None

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self) -> list[Any]:
        output: list[Any] = []
        for owner in self.OWNER_ORDER:
            output.extend(list(getattr(self.owners[owner], "setups", [])))
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "ml_opportunity_universe": {
                "policy": OPPORTUNITY_UNIVERSE_POLICY,
                "identity_policy": IDENTITY_NAMESPACE_POLICY,
                "counts": dict(sorted(self._counts.items())),
                "owners": self.OWNER_ORDER,
                "owner_priority": "NONE_ALL_COMPLETE_PLANS_ENTER_SHARED_ML_ARBITRATION",
            },
            **{
                owner.lower(): self.owners[owner].diagnostics
                for owner in self.OWNER_ORDER
            },
        }


MultiScaleScenarioBundle = EasyChartMLOpportunityUniverse
