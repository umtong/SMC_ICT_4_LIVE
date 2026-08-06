#!/usr/bin/env python3
"""Global-entry-slot wrappers for one shared multi-instrument account."""
from __future__ import annotations

from typing import Any

from global_entry_slot import GLOBAL_ENTRY_SLOT
from strategy_base import LiquidityResponseConfig
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v26_no_early_sponsored_ablation import NoEarlySponsoredParticipationStrategy
from strategy_v29b_external_displacement_fvg import ExternalDisplacementFvgStrategyV2
from strategy_v30_external_acceptance_retest import ExternalAcceptanceFirstRetestStrategy
from strategy_v31_impact_resiliency_reversal import ImpactResiliencyReversalStrategy
from strategy_v32_queue_pressure_release import QueuePressureReleaseStrategy


class GlobalEntrySlotMixin:
    """Acquire the shared slot only for a new entry bracket.

    Existing exit, reduction and protection paths remain untouched.  The slot is
    held from successful entry submission through order cancellation or position
    close.  The base strategy still owns its local one-intent checks.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        self._global_slot_owner = str(config.instrument_id)
        super().__init__(config)  # type: ignore[misc]
        self.diagnostics.update(
            {
                "global_slot_acquisitions": 0,
                "global_slot_conflicts": 0,
                "global_slot_releases": 0,
                "global_slot_release_mismatches": 0,
            },
        )

    def _slot_ts(self) -> int:
        return int(self.bars[-1]["ts"]) if getattr(self, "bars", None) else 0

    def _submit_price_capped_bracket(self, *args: Any, **kwargs: Any) -> bool:
        armed = kwargs.get("armed")
        row = kwargs.get("row") or {}
        ts_event = int(row.get("ts", self._slot_ts()))
        scenario_id = None
        if armed is not None:
            scenario_id = getattr(getattr(armed, "setup", None), "scenario_id", None)
        acquired = GLOBAL_ENTRY_SLOT.acquire(
            owner=self._global_slot_owner,
            ts_event=ts_event,
            reason="NEW_ENTRY_BRACKET_SUBMISSION",
            context={
                "scenario_id": scenario_id,
                "strategy": type(self).__name__,
                "instrument_id": self._global_slot_owner,
            },
        )
        if not acquired:
            self.diagnostics["global_slot_conflicts"] = int(self.diagnostics["global_slot_conflicts"]) + 1
            return False
        self.diagnostics["global_slot_acquisitions"] = int(self.diagnostics["global_slot_acquisitions"]) + 1
        submitted = super()._submit_price_capped_bracket(*args, **kwargs)  # type: ignore[misc]
        if not submitted:
            released = GLOBAL_ENTRY_SLOT.release(
                owner=self._global_slot_owner,
                ts_event=ts_event,
                reason="ENTRY_SUBMISSION_RETURNED_FALSE",
                context={"scenario_id": scenario_id, "strategy": type(self).__name__},
            )
            key = "global_slot_releases" if released else "global_slot_release_mismatches"
            self.diagnostics[key] = int(self.diagnostics[key]) + 1
        return bool(submitted)

    def _release_global_slot_if_idle(self, reason: str, event: Any | None = None) -> None:
        try:
            flat = self.portfolio.is_flat(self.config.instrument_id)
        except Exception:
            flat = False
        if not flat or bool(getattr(self, "entry_pending", False)):
            return
        ts_event = int(getattr(event, "ts_event", self._slot_ts()))
        released = GLOBAL_ENTRY_SLOT.release(
            owner=self._global_slot_owner,
            ts_event=ts_event,
            reason=reason,
            context={"strategy": type(self).__name__, "instrument_id": self._global_slot_owner},
        )
        key = "global_slot_releases" if released else "global_slot_release_mismatches"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()  # type: ignore[misc]
        self._release_global_slot_if_idle("LOCAL_TRADE_STATE_CLEARED")

    def on_position_closed(self, event: Any) -> None:
        super().on_position_closed(event)  # type: ignore[misc]
        self._release_global_slot_if_idle("POSITION_CLOSED", event)

    def on_order_rejected(self, event: Any) -> None:
        super().on_order_rejected(event)  # type: ignore[misc]
        self._release_global_slot_if_idle("ENTRY_ORDER_REJECTED", event)

    def on_order_denied(self, event: Any) -> None:
        parent = getattr(super(), "on_order_denied", None)
        if callable(parent):
            parent(event)
        self._release_global_slot_if_idle("ENTRY_ORDER_DENIED", event)

    def on_stop(self) -> None:
        super().on_stop()  # type: ignore[misc]
        self._release_global_slot_if_idle("STRATEGY_STOPPED")


class GlobalSlotV26Strategy(GlobalEntrySlotMixin, ScenarioValidEntryStrategy):
    pass


class GlobalSlotNoEarlySponsoredStrategy(GlobalEntrySlotMixin, NoEarlySponsoredParticipationStrategy):
    pass


class GlobalSlotV29bStrategy(GlobalEntrySlotMixin, ExternalDisplacementFvgStrategyV2):
    pass


class GlobalSlotV30Strategy(GlobalEntrySlotMixin, ExternalAcceptanceFirstRetestStrategy):
    pass


class GlobalSlotV31Strategy(GlobalEntrySlotMixin, ImpactResiliencyReversalStrategy):
    pass


class GlobalSlotV32Strategy(GlobalEntrySlotMixin, QueuePressureReleaseStrategy):
    pass


__all__ = [
    "GlobalSlotNoEarlySponsoredStrategy",
    "GlobalSlotV26Strategy",
    "GlobalSlotV29bStrategy",
    "GlobalSlotV30Strategy",
    "GlobalSlotV31Strategy",
    "GlobalSlotV32Strategy",
]
