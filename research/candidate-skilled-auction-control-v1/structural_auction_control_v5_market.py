"""Market-aware integration layer for structural auction control v5.

The event-time journey is still the only decision policy.  This adapter gives it
two responsibilities that belong at integration rather than inside individual
pattern sensors:

* propagate the completed BTC/ETH-led four-market control state to every sensor
  which understands common initiative;
* namespace raw plan identities by sensor, because independent causal engines
  legitimately start their own local counters at the same value.

No market-factor state creates a trade.  It may only prevent a local sensor from
fighting a live common shock.  No ranking score or outcome information is added.
"""
from __future__ import annotations

from dataclasses import replace
import math
import re
from typing import Any

from structural_auction_control_v2 import StructuralProposal, _descriptor, _price_geometry
from structural_auction_control_v5 import (
    ActivityBlock,
    JourneyEvidence,
    StructuralAuctionControlV5Bundle as _Base,
)


class StructuralAuctionControlV5MarketBundle(_Base):
    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._journey_by_source_plan: dict[tuple[str, str], JourneyEvidence] = {}
        self._namespaced_source_plans: dict[tuple[str, str], str] = {}
        self._market_factor_state: Any | None = None
        self._market_context_counts: dict[str, int] = {}

    def _minc(self, key: str) -> None:
        self._market_context_counts[key] = self._market_context_counts.get(key, 0) + 1

    def set_market_factor_state(self, state: Any | None) -> None:
        self._market_factor_state = state
        propagated = 0
        for _, sensor in self.sources:
            setter = getattr(sensor, "set_market_factor_state", None)
            if setter is None:
                continue
            setter(state)
            propagated += 1
        self._minc("market_factor_propagated" if propagated else "market_factor_no_sensor_consumer")

    def _proposal(self, plan, source: str):  # type: ignore[no-untyped-def]
        proposal = super()._proposal(plan, source)
        journey = self._journey_by_raw_plan.get(plan.plan_id)
        if journey is not None:
            self._journey_by_source_plan[(source, plan.plan_id)] = journey
        return proposal

    def _geometry_rank(self, proposal: StructuralProposal) -> tuple[Any, ...]:
        plan = proposal.plan
        entry, _, target = _price_geometry(plan)
        distance = abs(target - entry) if math.isfinite(entry) and math.isfinite(target) else math.inf
        descriptor = _descriptor(plan)
        journey = self._journey_by_source_plan.get((proposal.source, plan.plan_id))
        blocks: tuple[ActivityBlock, ...] = () if journey is None else journey.blocks
        late_progress = blocks[-1].progress if blocks else -math.inf
        late_efficiency = blocks[-1].path_efficiency if blocks else -math.inf
        return (
            self._PRIORITY[proposal.mechanism],
            self._destination_rank(descriptor),
            -late_progress,
            -late_efficiency,
            distance,
            proposal.observed_time_ns,
            proposal.source,
            plan.plan_id,
        )

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")

    def _namespace(self, proposal: StructuralProposal):  # type: ignore[no-untyped-def]
        raw = proposal.plan
        key = (proposal.source, raw.plan_id)
        if key in self._namespaced_source_plans:
            raise RuntimeError(f"sensor emitted duplicate raw plan identity {key!r}")
        plan_id = (
            f"sac-v5-{self._slug(proposal.source)}-"
            f"{self._slug(proposal.mechanism)}-{raw.plan_id}"
        )
        if plan_id in self._namespaced_source_plans.values():
            raise RuntimeError(f"global structural v5 plan collision {plan_id!r}")
        self._namespaced_source_plans[key] = plan_id
        journey = self._journey_by_source_plan.get(key)
        terminal = "UNKNOWN" if journey is None else journey.terminal_state
        return replace(
            raw,
            plan_id=plan_id,
            family=f"SAC_V5_{terminal}:{raw.family}",
            causal_event_id=(
                f"SAC_V5:{proposal.source}:{proposal.structure_id}:"
                f"{proposal.interaction_time_ns}:{raw.causal_event_id}"
            ),
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = super().diagnostics
        state = self._market_factor_state
        output["structural_auction_control_v5_market"] = {
            "counts": dict(sorted(self._market_context_counts.items())),
            "source_plan_identities": len(self._namespaced_source_plans),
            "active_common_state": None
            if state is None
            else {
                "side": getattr(getattr(state, "side", None), "name", None),
                "event_time_ns": getattr(state, "event_time_ns", None),
                "agreeing_symbols": getattr(state, "agreeing_symbols", ()),
                "sequence": getattr(state, "sequence", None),
            },
        }
        return output


MultiScaleScenarioBundle = StructuralAuctionControlV5MarketBundle
