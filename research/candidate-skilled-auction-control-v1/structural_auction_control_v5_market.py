"""Market-aware integration layer for structural auction control v5.

The event-time journey is still the only decision policy. This adapter gives it
five integration responsibilities:

* use the complete opportunity sensor plus the independently rigorous channel
  owner instead of replaying overlapping partial ancestors as separate sources;
* propagate the completed BTC/ETH-led four-market control state to every sensor
  which understands common initiative;
* namespace raw plan identities by sensor;
* keep lower-scale families from receiving a sixty-minute bar they do not own;
* begin the journey at the opening of the completed decision bar whose close
  owns the recorded interaction.

When two sensors describe the same completed journey, the more complete
mechanism wins first and the nearest still-valid destination offering at least
one gross R wins next. No market-factor state creates a trade, no trained score
or outcome information is used, and a larger advertised R never compensates for
a farther target.
"""
from __future__ import annotations

from dataclasses import replace
import math
import re
from typing import Any

from structural_auction_control_v2 import (
    StructuralProposal,
    _descriptor,
    _gross_rr,
    _number,
    _price_geometry,
)
from structural_auction_control_v5 import (
    ActivityBlock,
    EventTimeTape,
    JourneyEvidence,
    StructuralAuctionControlV5Bundle as _Base,
)


MINUTE_NS = 60_000_000_000


class OwnedTimeframeProxy:
    """Expose one sensor unchanged, but only on its declared causal clocks."""

    def __init__(self, sensor: Any, timeframes: frozenset[int]) -> None:
        self.sensor = sensor
        self.timeframes = timeframes

    def on_bar(self, timeframe_minutes: int, bar: Any):  # type: ignore[no-untyped-def]
        if timeframe_minutes not in self.timeframes:
            return []
        return self.sensor.on_bar(timeframe_minutes, bar)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.sensor, name)


class InteractionBarEventTimeTape(EventTimeTape):
    """Include the full completed decision bar which created the interaction."""

    def evaluate(self, plan: Any, mechanism: str) -> JourneyEvidence:
        original = int(_number(getattr(plan, "interaction_time_ns", 0), 0.0))
        decision_minutes = int(
            max(
                1.0,
                _number(getattr(plan, "decision_timeframe_minutes", 5), 5.0),
            )
        )
        event_start = original - decision_minutes * MINUTE_NS
        observed_plan = replace(plan, interaction_time_ns=event_start)
        evidence = super().evaluate(observed_plan, mechanism)
        return replace(evidence, interaction_time_ns=original)


class StructuralAuctionControlV5MarketBundle(_Base):
    _LOWER_SCALE_FAMILIES = (
        "horizontal_flip_response",
        "mature_diagonal_acceptance",
    )

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        source_map = dict(self.sources)
        self.sources = [
            ("CHANNEL_CONTROL", source_map["CHANNEL_CONTROL"]),
            ("COMPLETE_OPPORTUNITY", source_map["COMPLETE_OPPORTUNITY"]),
        ]
        for _, sensor in self.sources:
            for attribute in self._LOWER_SCALE_FAMILIES:
                family = getattr(sensor, attribute, None)
                if family is None or isinstance(family, OwnedTimeframeProxy):
                    continue
                setattr(
                    sensor,
                    attribute,
                    OwnedTimeframeProxy(family, frozenset({1, 5, 15})),
                )
        self.tape = InteractionBarEventTimeTape(tick_size)
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
        gross_rr = _gross_rr(plan)
        descriptor = _descriptor(plan)
        journey = self._journey_by_source_plan.get((proposal.source, plan.plan_id))
        blocks: tuple[ActivityBlock, ...] = () if journey is None else journey.blocks
        late_progress = blocks[-1].progress if blocks else -math.inf
        late_efficiency = blocks[-1].path_efficiency if blocks else -math.inf
        return (
            self._PRIORITY[proposal.mechanism],
            gross_rr if math.isfinite(gross_rr) else math.inf,
            distance,
            self._destination_rank(descriptor),
            -late_progress,
            -late_efficiency,
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
