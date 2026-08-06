#!/usr/bin/env python3
"""Candidate 05 v27 diagnostic: remove only the delayed-response gate."""
from __future__ import annotations

from typing import Any

from delayed_rejection_logic import DELAYED_CHOCH_BARS
from strategy_base import LiquidityResponseConfig
from strategy_v27 import DelayedRejectionStrategy


class NoDelayedResponseAblationStrategy(DelayedRejectionStrategy):
    """Send material unresolved access directly to the unchanged CHoCH gate."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics["delayed_response_stage_ablated"] = 0

    def _observe_unresolved_access(
        self,
        *,
        detector_scenario_id: str,
        event_time_ns: int,
        observed_time_ns: int,
        reference_price: float,
        details: dict[str, Any],
    ) -> None:
        previous_counter = self.delayed_rejection_counter
        super()._observe_unresolved_access(
            detector_scenario_id=detector_scenario_id,
            event_time_ns=event_time_ns,
            observed_time_ns=observed_time_ns,
            reference_price=reference_price,
            details=details,
        )
        if self.delayed_rejection_counter == previous_counter:
            return
        scenario_id = f"dlr-{self.delayed_rejection_counter:07d}"
        watch = self.delayed_rejection_watches.get(scenario_id)
        if watch is None:
            return
        watch.phase = "WAIT_DELAYED_CHOCH"
        watch.response_index = watch.created_index
        watch.choch_expires_index = watch.created_index + DELAYED_CHOCH_BARS
        watch.details["ablation"] = "REMOVE_DELAYED_RECLAIM_TAIL_DEPTH_STAGE"
        self.diagnostics["delayed_response_stage_ablated"] += 1
        self._transition(
            scenario_id,
            "DELAYED_RESPONSE_STAGE_ABLATED",
            event_time_ns,
            observed_time_ns,
            "WAIT_DELAYED_CHOCH",
            "MATERIAL_ACCESS_DIRECTLY_AWAITS_UNCHANGED_CHOCH",
            reference_price,
            dict(watch.details),
        )


__all__ = ["NoDelayedResponseAblationStrategy"]
