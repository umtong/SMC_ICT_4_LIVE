"""Flow-impact validated matching-scale liquidity delivery.

A structural break or sweep defines where control may transfer; aggressor flow
and realized price impact determine whether it actually did.  Activation is
therefore delayed until the one-minute candle sharing the completed 5m/15m
confirmation close has supplied the last constituent trade fields.

Accepted external breaks require aligned cumulative taker flow, net intended
price progress and at least one active directed progress minute across the break
and hold episode.  Sweep reversals require either continued adverse aggression
while price recovers (passive absorption), or aligned re-initiative whose price
impact per signed quote exceeds the sweep penetration impact.  A local entry
must then occur on a strictly later completed minute.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle, Side
from easychart_re1_delivery_draw import PendingExternalTransfer
from easychart_re1_delivery_draw_v3 import CausalLiquidityDrawV3
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation


DELIVERY_FLOW_IMPACT_RULE = (
    "EXTERNAL_METHOD:"
    "EXTERNAL_LIQUIDITY_DELIVERY_REQUIRES_CAUSAL_AGGRESSOR_FLOW_AND_PRICE_IMPACT_CONSISTENT_WITH_ACCEPTED_INITIATIVE_OR_CONTROL_TRANSFER"
)
STRICTLY_LATER_DELIVERY_ENTRY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_LOCAL_ENTRY_MUST_CLOSE_STRICTLY_AFTER_THE_COMPLETED_LIQUIDITY_DELIVERY_ACTIVATION_EVENT"
)
for _rule in (
    DELIVERY_FLOW_IMPACT_RULE,
    STRICTLY_LATER_DELIVERY_ENTRY_RULE,
):
    if _rule not in _contracts.EXTERNAL_RULES:
        _contracts.EXTERNAL_RULES += (_rule,)


@dataclass(slots=True)
class ProvisionalDeliveryActivation:
    pending: PendingExternalTransfer
    activation_time_ns: int
    activation_bar: Candle


class FlowValidatedLiquidityDraw(CausalLiquidityDrawV3):
    """Matching-scale draw activated only after complete constituent flow."""

    def __init__(self, symbol: str, tick_size: float) -> None:
        super().__init__(symbol, tick_size)
        self.flow_analyzer = CausalFlowAnalyzer(tick_size)
        self._provisional_activation: ProvisionalDeliveryActivation | None = None

    @staticmethod
    def _aligned(side: Side, value: float) -> bool:
        return value > 0.0 if side is Side.LONG else value < 0.0

    @staticmethod
    def _adverse(side: Side, value: float) -> bool:
        return value < 0.0 if side is Side.LONG else value > 0.0

    @staticmethod
    def _intended_progress(side: Side, start: float, end: float) -> float:
        return end - start if side is Side.LONG else start - end

    @staticmethod
    def _adverse_quote(side: Side, observation: FlowObservation) -> float:
        return (
            max(0.0, -observation.signed_taker_quote)
            if side is Side.LONG
            else max(0.0, observation.signed_taker_quote)
        )

    @staticmethod
    def _aligned_quote(side: Side, observation: FlowObservation) -> float:
        return (
            max(0.0, observation.signed_taker_quote)
            if side is Side.LONG
            else max(0.0, -observation.signed_taker_quote)
        )

    def _episode_observations(
        self,
        provisional: ProvisionalDeliveryActivation,
    ) -> list[FlowObservation]:
        pending = provisional.pending
        start = pending.event_time_ns - self.CONTEXT_MINUTES * 60 * 1_000_000_000
        return [
            item
            for item in self.flow_analyzer.history
            if start < item.ts_close_ns <= provisional.activation_time_ns
        ]

    def _acceptance_flow_valid(
        self,
        provisional: ProvisionalDeliveryActivation,
        observations: list[FlowObservation],
    ) -> tuple[bool, dict[str, Any]]:
        pending = provisional.pending
        cumulative = sum(item.signed_taker_quote for item in observations)
        progress = self._intended_progress(
            pending.side,
            observations[0].open,
            observations[-1].close,
        )
        aligned = [
            item
            for item in observations
            if item.active
            and item.directed
            and item.material_progress
            and self._aligned(pending.side, item.signed_taker_quote)
            and (
                item.body > 0.0
                if pending.side is Side.LONG
                else item.body < 0.0
            )
        ]
        valid = (
            self._aligned(pending.side, cumulative)
            and progress > 0.0
            and bool(aligned)
        )
        return valid, {
            "mechanism": "ACCEPTED_EXTERNAL_BREAK_ALIGNED_INITIATIVE",
            "episode_bars": len(observations),
            "cumulative_signed_taker_quote": cumulative,
            "net_price_progress": progress,
            "active_aligned_bars": len(aligned),
        }

    def _sweep_flow_valid(
        self,
        provisional: ProvisionalDeliveryActivation,
        observations: list[FlowObservation],
    ) -> tuple[bool, dict[str, Any]]:
        pending = provisional.pending
        bar = provisional.activation_bar
        adverse_quote = sum(
            self._adverse_quote(pending.side, item)
            for item in observations
        )
        aligned_quote = sum(
            self._aligned_quote(pending.side, item)
            for item in observations
        )
        absolute_quote = sum(
            abs(item.signed_taker_quote) for item in observations
        )
        cumulative = sum(item.signed_taker_quote for item in observations)
        penetration = (
            max(0.0, pending.source_pivot_price - pending.extreme)
            if pending.side is Side.LONG
            else max(0.0, pending.extreme - pending.source_pivot_price)
        )
        recovery = (
            bar.close - pending.extreme
            if pending.side is Side.LONG
            else pending.extreme - bar.close
        )
        adverse_impact = penetration / max(adverse_quote, 1e-18)
        recovery_impact = recovery / max(absolute_quote, 1e-18)
        active_directed = any(
            item.active and item.directed for item in observations
        )
        passive_absorption = (
            active_directed
            and adverse_quote > 0.0
            and penetration > 0.0
            and recovery > 0.0
            and self._adverse(pending.side, cumulative)
        )
        reinitiative = (
            active_directed
            and penetration > 0.0
            and recovery > 0.0
            and aligned_quote > adverse_quote
            and recovery_impact > adverse_impact
        )
        mechanism = (
            "EXTERNAL_SWEEP_PASSIVE_ABSORPTION"
            if passive_absorption
            else "EXTERNAL_SWEEP_EFFICIENT_REINITIATIVE"
            if reinitiative
            else "NONE"
        )
        return passive_absorption or reinitiative, {
            "mechanism": mechanism,
            "episode_bars": len(observations),
            "adverse_quote": adverse_quote,
            "aligned_quote": aligned_quote,
            "cumulative_signed_taker_quote": cumulative,
            "penetration": penetration,
            "recovery": recovery,
            "adverse_impact_per_quote": adverse_impact,
            "recovery_impact_per_quote": recovery_impact,
        }

    def _activate(
        self,
        pending: PendingExternalTransfer,
        time_ns: int,
        current_price: float,
    ) -> None:
        del current_price
        bar = self._current_completed_bar
        if bar is None or bar.ts_close_ns != time_ns:
            raise RuntimeError("delivery activation lost its completed confirmation bar")
        self._provisional_activation = ProvisionalDeliveryActivation(
            pending=pending,
            activation_time_ns=time_ns,
            activation_bar=bar,
        )
        self.pending = None
        self._inc("delivery_waiting_complete_constituent_flow")
        self._trace(
            "delivery_waiting_complete_constituent_flow",
            time_ns,
            side=pending.side.name,
            mode=pending.mode,
            source_pivot_id=pending.source_pivot_id,
            source_pivot_price=pending.source_pivot_price,
            activation_close=bar.close,
            rule_provenance=DELIVERY_FLOW_IMPACT_RULE,
        )

    def _discover_external_transfer(self, bar: Candle) -> None:
        if self._provisional_activation is not None:
            self._inc("external_discovery_deferred_during_provisional_activation")
            return
        super()._discover_external_transfer(bar)

    def _finalize_provisional(self, bar: Candle) -> None:
        provisional = self._provisional_activation
        if provisional is None or provisional.activation_time_ns > bar.ts_close_ns:
            return
        self._provisional_activation = None
        if provisional.activation_time_ns != bar.ts_close_ns:
            self._inc("delivery_provisional_missed_complete_constituent")
            return
        observations = self._episode_observations(provisional)
        if not observations:
            self._inc("delivery_provisional_missing_flow_history")
            return
        if provisional.pending.mode == "EXTERNAL_ACCEPTANCE_HELD":
            valid, evidence = self._acceptance_flow_valid(
                provisional,
                observations,
            )
        else:
            valid, evidence = self._sweep_flow_valid(
                provisional,
                observations,
            )
        if not valid:
            self._inc("delivery_rejected_without_flow_impact_transfer")
            self._trace(
                "delivery_rejected_without_flow_impact_transfer",
                bar.ts_close_ns,
                side=provisional.pending.side.name,
                mode=provisional.pending.mode,
                source_pivot_id=provisional.pending.source_pivot_id,
                rule_provenance=DELIVERY_FLOW_IMPACT_RULE,
                **evidence,
            )
            return

        prior = self._current_completed_bar
        self._current_completed_bar = provisional.activation_bar
        try:
            super()._activate(
                provisional.pending,
                provisional.activation_time_ns,
                provisional.activation_bar.close,
            )
        finally:
            self._current_completed_bar = prior
        if self.active is not None:
            self._inc("flow_impact_validated_delivery_activated")
            self._trace(
                "flow_impact_validated_delivery_activated",
                bar.ts_close_ns,
                side=self.active.side.name,
                source_mode=self.active.source_mode,
                source_pivot_id=self.active.source_pivot_id,
                target_pivot_id=self.active.target_pivot_id,
                target_price=self.active.target_price,
                rule_provenance=DELIVERY_FLOW_IMPACT_RULE,
                **evidence,
            )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> None:
        if timeframe_minutes == self.TRIGGER_MINUTES:
            self.flow_analyzer.observe(bar)
            self._finalize_provisional(bar)
            return
        super().on_bar(timeframe_minutes, bar)

    def allows(self, plan: V5TradePlan) -> bool:
        active = self.active
        return bool(
            active is not None
            and plan.side is active.side
            and plan.observed_time_ns > active.event_time_ns
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["flow_impact_delivery"] = {
            "provisional": None
            if self._provisional_activation is None
            else {
                "side": self._provisional_activation.pending.side.name,
                "mode": self._provisional_activation.pending.mode,
                "activation_time_ns": self._provisional_activation.activation_time_ns,
            },
            "flow": self.flow_analyzer.diagnostics,
            "rules": (
                DELIVERY_FLOW_IMPACT_RULE,
                STRICTLY_LATER_DELIVERY_ENTRY_RULE,
            ),
        }
        return output
