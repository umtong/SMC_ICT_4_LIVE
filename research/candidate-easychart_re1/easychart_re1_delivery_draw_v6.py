"""Mechanism-specific active flow for external sweep control transfer.

A completed external sweep can contain active trading unrelated to the claimed
transfer.  Passive absorption is demonstrated only when at least one active,
directed constituent actually carries adverse taker pressure while price
recovers.  Active re-initiative is demonstrated only when at least one active,
directed, material-progress constituent carries aligned taker flow and the
recovery remains more impact-efficient than the sweep penetration.

This assigns the activity evidence to the mechanism it is supposed to explain;
it adds no magnitude threshold, percentile, score or time filter.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from easychart_re1_delivery_draw_v4 import ProvisionalDeliveryActivation
from easychart_re1_delivery_draw_v5 import FlowValidatedLiquidityDrawV5
from easychart_re1_flow import FlowObservation


MECHANISM_SPECIFIC_ACTIVE_FLOW_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "PASSIVE_ABSORPTION_REQUIRES_ACTIVE_DIRECTED_ADVERSE_FLOW_AND_REINITIATIVE_REQUIRES_ACTIVE_DIRECTED_ALIGNED_MATERIAL_PROGRESS_WITHIN_THE_CONTROL_TRANSFER_EPISODE"
)
if MECHANISM_SPECIFIC_ACTIVE_FLOW_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (MECHANISM_SPECIFIC_ACTIVE_FLOW_RULE,)


class FlowValidatedLiquidityDrawV6(FlowValidatedLiquidityDrawV5):
    """Flow-impact delivery whose activity belongs to the claimed mechanism."""

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
            if pending.side.name == "LONG"
            else max(0.0, pending.extreme - pending.source_pivot_price)
        )
        recovery = (
            bar.close - pending.extreme
            if pending.side.name == "LONG"
            else pending.extreme - bar.close
        )
        adverse_impact = penetration / max(adverse_quote, 1e-18)
        recovery_impact = recovery / max(absolute_quote, 1e-18)
        active_adverse = [
            item
            for item in observations
            if item.active
            and item.directed
            and self._adverse(pending.side, item.signed_taker_quote)
        ]
        active_aligned_progress = [
            item
            for item in observations
            if item.active
            and item.directed
            and item.material_progress
            and self._aligned(pending.side, item.signed_taker_quote)
            and (
                item.body > 0.0
                if pending.side.name == "LONG"
                else item.body < 0.0
            )
        ]
        passive_absorption = (
            bool(active_adverse)
            and adverse_quote > 0.0
            and penetration > 0.0
            and recovery > 0.0
            and self._adverse(pending.side, cumulative)
        )
        reinitiative = (
            bool(active_aligned_progress)
            and penetration > 0.0
            and recovery > 0.0
            and aligned_quote > adverse_quote
            and recovery_impact > adverse_impact
        )
        mechanism = (
            "EXTERNAL_SWEEP_ACTIVE_ADVERSE_FLOW_ABSORBED"
            if passive_absorption
            else "EXTERNAL_SWEEP_ACTIVE_EFFICIENT_REINITIATIVE"
            if reinitiative
            else "NONE"
        )
        return passive_absorption or reinitiative, {
            "mechanism": mechanism,
            "episode_bars": len(observations),
            "active_adverse_bars": len(active_adverse),
            "active_aligned_progress_bars": len(active_aligned_progress),
            "adverse_quote": adverse_quote,
            "aligned_quote": aligned_quote,
            "cumulative_signed_taker_quote": cumulative,
            "penetration": penetration,
            "recovery": recovery,
            "adverse_impact_per_quote": adverse_impact,
            "recovery_impact_per_quote": recovery_impact,
            "mechanism_rule": MECHANISM_SPECIFIC_ACTIVE_FLOW_RULE,
        }

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["mechanism_specific_active_flow"] = {
            "passive_absorption": "ACTIVE_DIRECTED_ADVERSE_CONSTITUENT_REQUIRED",
            "reinitiative": "ACTIVE_DIRECTED_ALIGNED_MATERIAL_PROGRESS_CONSTITUENT_REQUIRED",
            "rule_provenance": MECHANISM_SPECIFIC_ACTIVE_FLOW_RULE,
        }
        return output
