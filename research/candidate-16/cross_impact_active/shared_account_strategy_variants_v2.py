"""Fixed shared-account mapping for Candidate-16 cross-impact continuation."""
from __future__ import annotations

from typing import Any, Type

from global_entry_slot_v3 import ENTRY_INTENT
from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as _wrappers
from strategy_cross_impact_continuation import LaggedCrossImpactContinuationStrategy

# Reuse the final lifecycle wrapper and the exact coordinator reset/audit path
# already consumed by the existing shared Nautilus runner.
_wrappers.SHARED_ACCOUNT_ENTRY_COORDINATOR = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
SharedAccountEntryLifecycleMixin = _wrappers.SharedAccountEntryLifecycleMixin


class DirectSubmitGlobalSlotMixin:
    """Reserve the global slot for base-strategy direct bracket submission."""

    def _submit_entry(self, setup: Any, row: dict[str, Any]) -> bool:
        ts_event = int(row.get("ts", self._shared_slot_ts()))
        was_reentry = (
            FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.owner == self._shared_slot_owner
            and FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.phase == ENTRY_INTENT
        )
        acquired = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.acquire_entry_intent(
            owner=self._shared_slot_owner,
            ts_event=ts_event,
            reason="CROSS_IMPACT_DIRECT_BRACKET_SUBMISSION",
            context={
                "scenario_id": getattr(setup, "scenario_id", None),
                "strategy": type(self).__name__,
                "instrument_id": self._shared_slot_owner,
            },
        )
        if not acquired:
            self.diagnostics["shared_slot_conflicts"] = int(
                self.diagnostics["shared_slot_conflicts"],
            ) + 1
            scenario_id = getattr(setup, "scenario_id", None)
            if scenario_id is not None:
                self._transition(
                    scenario_id,
                    "GLOBAL_ENTRY_SLOT_CONFLICT",
                    ts_event,
                    ts_event,
                    "CLOSED",
                    "ANOTHER_PROJECT_SYMBOL_OWNS_NEW_ENTRY_OR_POSITION",
                    float(row.get("close", 0.0)),
                    {"owner": FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.owner},
                )
            if getattr(self, "pending", None) is setup:
                self.pending = None
            return False

        key = "shared_slot_reentries" if was_reentry else "shared_slot_acquisitions"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1
        submitted = bool(super()._submit_entry(setup, row))  # type: ignore[misc]
        if not submitted:
            self._release_shared_slot(
                reason="CROSS_IMPACT_ENTRY_SUBMISSION_RETURNED_FALSE",
                ts_event=ts_event,
                context={"scenario_id": getattr(setup, "scenario_id", None)},
            )
        return submitted


class FinalSharedCrossImpactStrategy(
    DirectSubmitGlobalSlotMixin,
    SharedAccountEntryLifecycleMixin,
    LaggedCrossImpactContinuationStrategy,
):
    """Cross-impact alpha with one globally audited executable slot."""


def _variant(name: str) -> type:
    return type(
        name,
        (FinalSharedCrossImpactStrategy,),
        {
            "__module__": __name__,
            "__doc__": f"Candidate-16 fixed cross-impact variant for {name}.",
        },
    )


_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
for _symbol in _SYMBOLS:
    globals()[f"FinalSharedCrossImpact{_symbol}Strategy"] = _variant(
        f"FinalSharedCrossImpact{_symbol}Strategy",
    )


WINNER = (
    "strategy_cross_impact_continuation:"
    "LaggedCrossImpactContinuationStrategy"
)
WINNER_TO_FAMILY = {WINNER: "candidate16-cross-impact"}


def final_shared_strategy_class_name(winner: str, symbol: str) -> str:
    if winner != WINNER:
        raise ValueError(f"unsupported validated winner: {winner}")
    if symbol not in _SYMBOLS:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"FinalSharedCrossImpact{symbol}Strategy"


def final_shared_strategy_path(winner: str, symbol: str) -> str:
    return f"{__name__}:{final_shared_strategy_class_name(winner, symbol)}"


def final_shared_strategy_class(winner: str, symbol: str) -> Type:
    return globals()[final_shared_strategy_class_name(winner, symbol)]


__all__ = [
    "WINNER",
    "WINNER_TO_FAMILY",
    "DirectSubmitGlobalSlotMixin",
    "FinalSharedCrossImpactStrategy",
    "final_shared_strategy_class",
    "final_shared_strategy_class_name",
    "final_shared_strategy_path",
    *[f"FinalSharedCrossImpact{symbol}Strategy" for symbol in _SYMBOLS],
]
