"""Mechanism-owned EasyChart auction controller.

This candidate deliberately replaces the broad plan lattice and score router with
only two complete auction decisions:

1. A confirmed channel liquidity sweep which closes back inside, remains inside
   on the next completed five-minute bar, and then receives a first later
   one-minute OB/FVG or causal aggressor-flow response.
2. A channel control transfer which closes outside, holds outside, makes its
   first detached return, and agrees with the active liquidity-delivery draw.

Each owner produces an immutable full-position entry/stop/target plan.  The
rejection stop belongs to the complete sweep/decision swing.  The acceptance
stop belongs to the projected edge, completed return wick, and breakout-wave
origin.  Targets remain the first pre-existing opposing 5m/15m structure or
channel objective selected by the inherited natural geometry.

The module does not add a fitted score, PnL-dependent rule, trade quota, clock
exit, partial exit, stop movement, target movement, symbol rule, or exposure
cap.  The NautilusTrader execution/accounting layer remains unchanged.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, StructureFamily, V5TradePlan
from domain import Candle
from easychart_re1_channel_rejection_hold import HeldChannelRejectionMicroEngine
from easychart_re1_delivery_channel_acceptance_v5 import (
    EasyChartRE1DeliveryChannelAcceptanceV5Bundle,
)
from scenario_bundle_v5 import AuditFrame


CHANNEL_REJECTION_OWNER_RULE = (
    "RESEARCH_HYPOTHESIS:CHANNEL_REJECTION_OWNER_REQUIRES_CHANNEL_CONTEXT_"
    "LIQUIDITY_SWEEP_RECLAIM_NEXT_DECISION_BAR_HOLD_AND_FIRST_LATER_RESPONSE"
)
CHANNEL_ACCEPTANCE_OWNER_RULE = (
    "RESEARCH_HYPOTHESIS:CHANNEL_ACCEPTANCE_OWNER_REQUIRES_OUTSIDE_CLOSE_"
    "NEXT_DECISION_BAR_HOLD_FIRST_DETACHED_RETURN_STRUCTURAL_WAVE_STOP_AND_"
    "ACTIVE_LIQUIDITY_DELIVERY_ALIGNMENT"
)
CAUSAL_EPISODE_OWNER_RULE = (
    "IMPLEMENTATION_VALIDITY:ONE_CHANNEL_INTERACTION_PRICE_EPISODE_HAS_ONE_"
    "EXECUTABLE_OWNER_WITH_SIMULTANEOUS_ACCEPTANCE_PRIORITY"
)
for _rule in (
    CHANNEL_REJECTION_OWNER_RULE,
    CHANNEL_ACCEPTANCE_OWNER_RULE,
    CAUSAL_EPISODE_OWNER_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


def _text(value: Any) -> str:
    return str(getattr(value, "value", value)).upper()


def _plan_has_channel(plan: V5TradePlan) -> bool:
    fields = (
        _text(plan.higher_zone_kind),
        _text(plan.lower_zone_kind),
        plan.higher_zone_id.upper(),
        plan.lower_zone_id.upper(),
        plan.scale_name.upper(),
    )
    return any("CHANNEL" in value for value in fields)


class ChannelRejectionOwnerEngine(HeldChannelRejectionMicroEngine):
    """Allow only a channel fakeout/trap to originate a reversal plan."""

    def _selected_clusters(self, bar: Candle, previous: Candle):  # type: ignore[no-untyped-def]
        selected = super()._selected_clusters(bar, previous)
        output = []
        for context, members, previous_zone in selected:
            if any(member.family is StructureFamily.CHANNEL for member in members):
                output.append((context, members, previous_zone))
                continue
            self._inc("non_channel_interaction_not_owned")
            self._trace(
                "non_channel_interaction_not_owned",
                bar.ts_close_ns,
                context_zone_id=context.zone_id,
                context_kinds=[_text(member.kind) for member in members],
                rule_provenance=CHANNEL_REJECTION_OWNER_RULE,
            )
        return output

    def _create_setup(self, *, path: ScenarioPath, **kwargs: Any):  # type: ignore[no-untyped-def]
        if path is not ScenarioPath.REJECTION:
            bar = kwargs["bar"]
            context = kwargs["context"]
            self._inc(f"channel_{path.value.lower()}_not_owned_by_rejection")
            self._trace(
                "channel_path_not_owned_by_rejection",
                bar.ts_close_ns,
                context_zone_id=context.zone_id,
                deferred_path=path.value,
                rule_provenance=CHANNEL_REJECTION_OWNER_RULE,
            )
            return None
        return super()._create_setup(path=path, **kwargs)


class ChannelRejectionOwnerBundle:
    """One 15m/5m/1m channel-rejection engine with audit compatibility."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.engine = ChannelRejectionOwnerEngine(
            symbol,
            tick_size,
            scale_name="CHANNEL_REJECTION_OWNER",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = {
            60: AuditFrame(60),
            15: AuditFrame(15),
            5: AuditFrame(5),
            1: AuditFrame(1),
        }
        self._audit_offset = 0
        self._plans: list[V5TradePlan] = []
        self._plan_id_map: dict[str, str] = {}

    def _sync_audit(self) -> None:
        for zone in self.engine.audit_zones[self._audit_offset :]:
            timeframe = getattr(zone, "timeframe_minutes", 1)
            destination = timeframe if timeframe in self.detectors else 5
            self.detectors[destination].register(zone)
        self._audit_offset = len(self.engine.audit_zones)

    def _namespace(self, plan: V5TradePlan) -> V5TradePlan:
        if plan.plan_id in self._plan_id_map:
            raise RuntimeError(f"duplicate rejection raw plan id {plan.plan_id!r}")
        plan_id = f"sac-v1-rejection-{plan.plan_id}"
        self._plan_id_map[plan.plan_id] = plan_id
        return replace(
            plan,
            plan_id=plan_id,
            causal_event_id=f"SAC_V1_REJECTION:{plan.causal_event_id}",
            family=f"SAC_V1_CHANNEL_REJECTION:{plan.family}",
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes in self.detectors:
            self.detectors[timeframe_minutes].on_bar(bar)
        raw: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            raw = self.engine.on_bar(timeframe_minutes, bar)
        self._sync_audit()
        output = [
            self._namespace(plan)
            for plan in raw
            if plan.scenario_path == ScenarioPath.REJECTION.value
            and _plan_has_channel(plan)
        ]
        self._plans.extend(output)
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        rows = self.engine.drain_trace()
        for row in rows:
            for key in ("plan_id", "suppressed_plan_id", "owner_plan_id"):
                value = row.get(key)
                if isinstance(value, str) and value in self._plan_id_map:
                    row[key] = self._plan_id_map[value]
        return rows

    def find_zone(self, zone_id: str) -> Any | None:
        for detector in self.detectors.values():
            for zone in detector.zones:
                if zone.zone_id == zone_id:
                    return zone
        return self.engine.find_zone(zone_id)

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return list(self.engine.setups)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "owner": "CHANNEL_SWEEP_RECLAIM_NEXT_BAR_HOLD_FIRST_RESPONSE",
            "engine": self.engine.diagnostics,
            "structure": self.engine.structure.diagnostics,
            "rules": (CHANNEL_REJECTION_OWNER_RULE,),
        }


class SkilledAuctionControlV1Bundle:
    """One executable stream from two non-overlapping channel mechanisms."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.rejection = ChannelRejectionOwnerBundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.acceptance = EasyChartRE1DeliveryChannelAcceptanceV5Bundle(
            symbol,
            tick_size,
            minimum_gross_rr,
        )
        self.detectors = self.rejection.detectors
        self._acceptance_audit_offsets = {
            timeframe: 0 for timeframe in self.acceptance.detectors
        }
        self._acceptance_plan_map: dict[str, str] = {}
        self._plans: list[V5TradePlan] = []
        self._claimed: list[tuple[int, float, float, str]] = []
        self._counts: dict[str, int] = {}
        self._trace: list[dict[str, Any]] = []

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _sync_acceptance_audit(self) -> None:
        for timeframe, source in self.acceptance.detectors.items():
            start = self._acceptance_audit_offsets[timeframe]
            for zone in source.zones[start:]:
                destination = timeframe if timeframe in self.detectors else 5
                self.detectors[destination].register(zone)
            self._acceptance_audit_offsets[timeframe] = len(source.zones)

    @staticmethod
    def _same_episode(left: V5TradePlan, right: V5TradePlan, tick_size: float) -> bool:
        return (
            left.symbol == right.symbol
            and left.interaction_time_ns == right.interaction_time_ns
            and max(left.overlap_lower, right.overlap_lower)
            <= min(left.overlap_upper, right.overlap_upper) + tick_size
        )

    def _already_claimed(self, plan: V5TradePlan) -> str | None:
        for interaction_time_ns, lower, upper, owner in self._claimed:
            if interaction_time_ns != plan.interaction_time_ns:
                continue
            if max(lower, plan.overlap_lower) <= min(upper, plan.overlap_upper) + self.tick_size:
                return owner
        return None

    def _claim(self, owner: str, plan: V5TradePlan) -> None:
        self._claimed.append(
            (
                plan.interaction_time_ns,
                plan.overlap_lower,
                plan.overlap_upper,
                owner,
            )
        )

    def _namespace_acceptance(self, plan: V5TradePlan) -> V5TradePlan:
        if plan.plan_id in self._acceptance_plan_map:
            raise RuntimeError(f"duplicate acceptance raw plan id {plan.plan_id!r}")
        plan_id = f"sac-v1-acceptance-{plan.plan_id}"
        self._acceptance_plan_map[plan.plan_id] = plan_id
        return replace(
            plan,
            plan_id=plan_id,
            causal_event_id=f"SAC_V1_ACCEPTANCE:{plan.causal_event_id}",
            family=f"SAC_V1_CHANNEL_ACCEPTANCE:{plan.family}",
        )

    @staticmethod
    def _owned_acceptance(plan: V5TradePlan) -> bool:
        return (
            plan.scenario_path == ScenarioPath.ACCEPTANCE.value
            and plan.scale_name == "DELIVERY_CHANNEL_ACCEPTANCE"
            and _plan_has_channel(plan)
        )

    def _route(
        self,
        rejection: list[V5TradePlan],
        acceptance_raw: list[V5TradePlan],
    ) -> list[V5TradePlan]:
        acceptance = [
            self._namespace_acceptance(plan)
            for plan in acceptance_raw
            if self._owned_acceptance(plan)
        ]
        output: list[V5TradePlan] = []

        # A completed accepted transfer is the stronger interpretation when both
        # owners emit the same completed boundary episode on the same bar.
        for plan in sorted(
            acceptance,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            claimed = self._already_claimed(plan)
            if claimed is not None:
                self._inc("acceptance_suppressed_claimed_episode")
                self._trace.append(
                    {
                        "scenario_kind": "acceptance_suppressed_claimed_episode",
                        "event_time_ns": plan.observed_time_ns,
                        "plan_id": plan.plan_id,
                        "interaction_time_ns": plan.interaction_time_ns,
                        "previous_owner": claimed,
                        "rule_provenance": CAUSAL_EPISODE_OWNER_RULE,
                    }
                )
                continue
            self._claim("ACCEPTANCE", plan)
            output.append(plan)
            self._inc("acceptance_owned")

        for plan in sorted(
            rejection,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            simultaneous = next(
                (
                    existing
                    for existing in output
                    if self._same_episode(plan, existing, self.tick_size)
                ),
                None,
            )
            if simultaneous is not None:
                self._inc("simultaneous_rejection_suppressed_by_acceptance")
                self._trace.append(
                    {
                        "scenario_kind": "simultaneous_episode_owned_by_acceptance",
                        "event_time_ns": plan.observed_time_ns,
                        "suppressed_plan_id": plan.plan_id,
                        "owner_plan_id": simultaneous.plan_id,
                        "interaction_time_ns": plan.interaction_time_ns,
                        "rule_provenance": CAUSAL_EPISODE_OWNER_RULE,
                    }
                )
                continue
            claimed = self._already_claimed(plan)
            if claimed is not None:
                self._inc("rejection_suppressed_claimed_episode")
                continue
            self._claim("REJECTION", plan)
            output.append(plan)
            self._inc("rejection_owned")

        output.sort(
            key=lambda item: (
                item.interaction_time_ns,
                0 if item.scenario_path == ScenarioPath.ACCEPTANCE.value else 1,
                item.observed_time_ns,
                item.plan_id,
            )
        )
        self._plans.extend(output)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        rejection = self.rejection.on_bar(timeframe_minutes, bar)
        acceptance_raw = self.acceptance.on_bar(timeframe_minutes, bar)
        self._sync_acceptance_audit()
        return self._route(rejection, acceptance_raw)

    def drain_trace(self) -> list[dict[str, Any]]:
        acceptance_trace = self.acceptance.drain_trace()
        for row in acceptance_trace:
            for key in ("plan_id", "suppressed_plan_id", "owner_plan_id"):
                value = row.get(key)
                if isinstance(value, str) and value in self._acceptance_plan_map:
                    row[key] = self._acceptance_plan_map[value]
        output = self.rejection.drain_trace() + acceptance_trace + self._trace
        self._trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self.rejection.find_zone(zone_id) or self.acceptance.find_zone(zone_id)

    @property
    def plans(self) -> list[V5TradePlan]:
        return list(self._plans)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return list(self.rejection.setups) + list(self.acceptance.setups)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "skilled_auction_control_v1": {
                "counts": dict(sorted(self._counts.items())),
                "rejection_owner": "CHANNEL_SWEEP_RECLAIM_NEXT_BAR_HOLD_FIRST_RESPONSE",
                "acceptance_owner": "DRAW_ALIGNED_CHANNEL_CONTROL_TRANSFER_FIRST_RETURN",
                "rules": (
                    CHANNEL_REJECTION_OWNER_RULE,
                    CHANNEL_ACCEPTANCE_OWNER_RULE,
                    CAUSAL_EPISODE_OWNER_RULE,
                ),
            },
            "rejection": self.rejection.diagnostics,
            "acceptance": self.acceptance.diagnostics,
        }


MultiScaleScenarioBundle = SkilledAuctionControlV1Bundle
