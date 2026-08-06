#!/usr/bin/env python3
"""Shared-account wrappers with audited intent-to-position lifecycle."""
from __future__ import annotations

from typing import Any

from global_entry_slot_v3 import ENTRY_INTENT
from global_entry_slot_v3 import POSITION_CLOSED_AWAIT_RELEASE
from global_entry_slot_v3 import SHARED_ACCOUNT_ENTRY_COORDINATOR
from strategy_base import LiquidityResponseConfig
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v26_no_early_sponsored_ablation import NoEarlySponsoredParticipationStrategy
from strategy_v29b_external_displacement_fvg import ExternalDisplacementFvgStrategyV2
from strategy_v30_external_acceptance_retest import ExternalAcceptanceFirstRetestStrategy
from strategy_v31_impact_resiliency_reversal import ImpactResiliencyReversalStrategy
from strategy_v32_queue_pressure_release import QueuePressureReleaseStrategy


class SharedAccountEntryLifecycleMixin:
    """Reserve the one global slot for a new entry and its resulting position."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        self._shared_slot_owner = str(config.instrument_id)
        super().__init__(config)  # type: ignore[misc]
        self.diagnostics.update(
            {
                "shared_slot_acquisitions": 0,
                "shared_slot_reentries": 0,
                "shared_slot_conflicts": 0,
                "shared_slot_position_opens": 0,
                "shared_slot_position_open_mismatches": 0,
                "shared_slot_position_closes": 0,
                "shared_slot_position_close_mismatches": 0,
                "shared_slot_releases": 0,
                "shared_slot_release_mismatches": 0,
            },
        )

    def _shared_slot_ts(self) -> int:
        return int(self.bars[-1]["ts"]) if getattr(self, "bars", None) else 0

    def _submit_price_capped_bracket(self, *args: Any, **kwargs: Any) -> bool:
        armed = kwargs.get("armed")
        row = kwargs.get("row") or {}
        ts_event = int(row.get("ts", self._shared_slot_ts()))
        scenario_id = getattr(getattr(armed, "setup", None), "scenario_id", None)
        was_reentry = (
            SHARED_ACCOUNT_ENTRY_COORDINATOR.owner == self._shared_slot_owner
            and SHARED_ACCOUNT_ENTRY_COORDINATOR.phase == ENTRY_INTENT
        )
        acquired = SHARED_ACCOUNT_ENTRY_COORDINATOR.acquire_entry_intent(
            owner=self._shared_slot_owner,
            ts_event=ts_event,
            reason="NEW_ENTRY_BRACKET_SUBMISSION",
            context={
                "scenario_id": scenario_id,
                "strategy": type(self).__name__,
                "instrument_id": self._shared_slot_owner,
            },
        )
        if not acquired:
            self.diagnostics["shared_slot_conflicts"] = int(self.diagnostics["shared_slot_conflicts"]) + 1
            return False
        key = "shared_slot_reentries" if was_reentry else "shared_slot_acquisitions"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1
        submitted = bool(super()._submit_price_capped_bracket(*args, **kwargs))  # type: ignore[misc]
        if not submitted:
            self._release_shared_slot(
                reason="ENTRY_SUBMISSION_RETURNED_FALSE",
                ts_event=ts_event,
                context={"scenario_id": scenario_id, "strategy": type(self).__name__},
            )
        return submitted

    def _release_shared_slot(
        self,
        *,
        reason: str,
        ts_event: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> bool:
        if SHARED_ACCOUNT_ENTRY_COORDINATOR.owner != self._shared_slot_owner:
            return False
        released = SHARED_ACCOUNT_ENTRY_COORDINATOR.release(
            owner=self._shared_slot_owner,
            ts_event=self._shared_slot_ts() if ts_event is None else int(ts_event),
            reason=reason,
            context={
                "strategy": type(self).__name__,
                "instrument_id": self._shared_slot_owner,
                **(context or {}),
            },
        )
        key = "shared_slot_releases" if released else "shared_slot_release_mismatches"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1
        return released

    def _release_shared_slot_if_idle(self, reason: str, event: Any | None = None) -> None:
        try:
            flat = self.portfolio.is_flat(self.config.instrument_id)
        except Exception:
            flat = False
        if not flat or bool(getattr(self, "entry_pending", False)):
            return
        phase = SHARED_ACCOUNT_ENTRY_COORDINATOR.phase
        if phase not in {ENTRY_INTENT, POSITION_CLOSED_AWAIT_RELEASE}:
            return
        self._release_shared_slot(
            reason=reason,
            ts_event=int(getattr(event, "ts_event", self._shared_slot_ts())),
        )

    def on_position_opened(self, event: Any) -> None:
        ts_event = int(getattr(event, "ts_event", self._shared_slot_ts()))
        transitioned = SHARED_ACCOUNT_ENTRY_COORDINATOR.position_opened(
            owner=self._shared_slot_owner,
            ts_event=ts_event,
            reason="NAUTILUS_POSITION_OPENED",
            context={"strategy": type(self).__name__, "event": str(event)},
        )
        key = "shared_slot_position_opens" if transitioned else "shared_slot_position_open_mismatches"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1
        super().on_position_opened(event)  # type: ignore[misc]

    def on_position_closed(self, event: Any) -> None:
        ts_event = int(getattr(event, "ts_event", self._shared_slot_ts()))
        transitioned = SHARED_ACCOUNT_ENTRY_COORDINATOR.position_closed(
            owner=self._shared_slot_owner,
            ts_event=ts_event,
            reason="NAUTILUS_POSITION_CLOSED",
            context={"strategy": type(self).__name__, "event": str(event)},
        )
        key = "shared_slot_position_closes" if transitioned else "shared_slot_position_close_mismatches"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1
        super().on_position_closed(event)  # type: ignore[misc]
        self._release_shared_slot_if_idle("POSITION_CLOSED_AND_LOCAL_STATE_CLEARED", event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()  # type: ignore[misc]
        self._release_shared_slot_if_idle("LOCAL_TRADE_STATE_CLEARED")

    def on_order_rejected(self, event: Any) -> None:
        super().on_order_rejected(event)  # type: ignore[misc]
        self._release_shared_slot_if_idle("ENTRY_ORDER_REJECTED", event)

    def on_order_denied(self, event: Any) -> None:
        parent = getattr(super(), "on_order_denied", None)
        if callable(parent):
            parent(event)
        self._release_shared_slot_if_idle("ENTRY_ORDER_DENIED", event)

    def on_stop(self) -> None:
        super().on_stop()  # type: ignore[misc]
        self._release_shared_slot_if_idle("STRATEGY_STOPPED")


class SharedAccountV26Strategy(SharedAccountEntryLifecycleMixin, ScenarioValidEntryStrategy):
    pass


class SharedAccountNoEarlySponsoredStrategy(
    SharedAccountEntryLifecycleMixin,
    NoEarlySponsoredParticipationStrategy,
):
    pass


class SharedAccountV29bStrategy(
    SharedAccountEntryLifecycleMixin,
    ExternalDisplacementFvgStrategyV2,
):
    pass


class SharedAccountV30Strategy(
    SharedAccountEntryLifecycleMixin,
    ExternalAcceptanceFirstRetestStrategy,
):
    pass


class SharedAccountV31Strategy(
    SharedAccountEntryLifecycleMixin,
    ImpactResiliencyReversalStrategy,
):
    pass


class SharedAccountV32Strategy(
    SharedAccountEntryLifecycleMixin,
    QueuePressureReleaseStrategy,
):
    pass


__all__ = [
    "SharedAccountEntryLifecycleMixin",
    "SharedAccountNoEarlySponsoredStrategy",
    "SharedAccountV26Strategy",
    "SharedAccountV29bStrategy",
    "SharedAccountV30Strategy",
    "SharedAccountV31Strategy",
    "SharedAccountV32Strategy",
]
