#!/usr/bin/env python3
"""Ownership-checked global-slot wrappers for the shared account."""
from __future__ import annotations

from typing import Any

from global_entry_slot import GLOBAL_ENTRY_SLOT
from strategy_base import LiquidityResponseConfig
from strategy_global_slot_wrappers import GlobalEntrySlotMixin
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v26_no_early_sponsored_ablation import NoEarlySponsoredParticipationStrategy
from strategy_v29b_external_displacement_fvg import ExternalDisplacementFvgStrategyV2
from strategy_v30_external_acceptance_retest import ExternalAcceptanceFirstRetestStrategy
from strategy_v31_impact_resiliency_reversal import ImpactResiliencyReversalStrategy
from strategy_v32_queue_pressure_release import QueuePressureReleaseStrategy


class OwnershipCheckedGlobalEntrySlotMixin(GlobalEntrySlotMixin):
    """Release only when this strategy actually owns the process-global slot."""

    def _release_global_slot_if_idle(self, reason: str, event: Any | None = None) -> None:
        try:
            flat = self.portfolio.is_flat(self.config.instrument_id)
        except Exception:
            flat = False
        if not flat or bool(getattr(self, "entry_pending", False)):
            return
        if GLOBAL_ENTRY_SLOT.owner != self._global_slot_owner:
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


class GlobalSlotV26StrategyV2(OwnershipCheckedGlobalEntrySlotMixin, ScenarioValidEntryStrategy):
    pass


class GlobalSlotNoEarlySponsoredStrategyV2(
    OwnershipCheckedGlobalEntrySlotMixin,
    NoEarlySponsoredParticipationStrategy,
):
    pass


class GlobalSlotV29bStrategyV2(
    OwnershipCheckedGlobalEntrySlotMixin,
    ExternalDisplacementFvgStrategyV2,
):
    pass


class GlobalSlotV30StrategyV2(
    OwnershipCheckedGlobalEntrySlotMixin,
    ExternalAcceptanceFirstRetestStrategy,
):
    pass


class GlobalSlotV31StrategyV2(
    OwnershipCheckedGlobalEntrySlotMixin,
    ImpactResiliencyReversalStrategy,
):
    pass


class GlobalSlotV32StrategyV2(
    OwnershipCheckedGlobalEntrySlotMixin,
    QueuePressureReleaseStrategy,
):
    pass


__all__ = [
    "GlobalSlotNoEarlySponsoredStrategyV2",
    "GlobalSlotV26StrategyV2",
    "GlobalSlotV29bStrategyV2",
    "GlobalSlotV30StrategyV2",
    "GlobalSlotV31StrategyV2",
    "GlobalSlotV32StrategyV2",
    "OwnershipCheckedGlobalEntrySlotMixin",
]
