"""Execution bindings which enforce one trade per intrinsic causal episode."""
from __future__ import annotations

from typing import Any

from execution_re1_flow import EasyChartRE1FlowStrategy
from execution_shadow_ml3v3 import EasyChartML3V3ShadowStrategy


class _OneCausalEpisodeMixin:
    """Prevent delayed alternative geometries from re-trading one event."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.claimed_intrinsic_events: set[str] = set()

    def _submit_plan(self, instrument_id: Any, plan: Any) -> bool:
        event_id = str(getattr(plan, "causal_event_id", ""))
        if not event_id:
            raise RuntimeError("intrinsic plan is missing causal_event_id")
        if event_id in self.claimed_intrinsic_events:
            self._record(
                "intrinsic_plan_rejected_duplicate_causal_event",
                plan_id=plan.plan_id,
                causal_event_id=event_id,
                instrument_id=str(instrument_id),
            )
            return False
        submitted = super()._submit_plan(instrument_id, plan)
        if submitted:
            self.claimed_intrinsic_events.add(event_id)
            self._record(
                "intrinsic_causal_event_claimed",
                plan_id=plan.plan_id,
                causal_event_id=event_id,
                instrument_id=str(instrument_id),
            )
        return submitted


class IntrinsicAuctionExecutionStrategy(_OneCausalEpisodeMixin, EasyChartRE1FlowStrategy):
    pass


class IntrinsicAuctionShadowStrategy(_OneCausalEpisodeMixin, EasyChartML3V3ShadowStrategy):
    def _baseline_context_allows(self, instrument_id: Any, plan: Any) -> bool:
        # This candidate is not an amendment to RE1.  Shadow mode must expose
        # every complete intrinsic-auction plan rather than silently reusing
        # the failed predecessor's context router.
        return True
