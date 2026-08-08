"""Fixed shared-account mapping for Candidate-16 SOL seesaw rotation."""
from __future__ import annotations

from typing import Any, Type

from global_entry_slot_v3 import ENTRY_INTENT
from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as _wrappers
from strategy_sol_seesaw_rotation import SolSeesawFlowRotationStrategy

_wrappers.SHARED_ACCOUNT_ENTRY_COORDINATOR = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
SharedAccountEntryLifecycleMixin = _wrappers.SharedAccountEntryLifecycleMixin


class DirectSubmitGlobalSlotMixin:
    def _submit_entry(self, setup: Any, row: dict[str, Any]) -> bool:
        ts_event = int(row.get("ts", self._shared_slot_ts()))
        was_reentry = (
            FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.owner == self._shared_slot_owner
            and FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.phase == ENTRY_INTENT
        )
        acquired = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.acquire_entry_intent(
            owner=self._shared_slot_owner,
            ts_event=ts_event,
            reason="SOL_SEESAW_DIRECT_BRACKET_SUBMISSION",
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
            if getattr(self, "pending", None) is setup:
                self.pending = None
            return False
        key = "shared_slot_reentries" if was_reentry else "shared_slot_acquisitions"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1
        submitted = bool(super()._submit_entry(setup, row))  # type: ignore[misc]
        if not submitted:
            self._release_shared_slot(
                reason="SOL_SEESAW_ENTRY_SUBMISSION_RETURNED_FALSE",
                ts_event=ts_event,
                context={"scenario_id": getattr(setup, "scenario_id", None)},
            )
        return submitted


class FinalSharedSolSeesawStrategy(
    DirectSubmitGlobalSlotMixin,
    SharedAccountEntryLifecycleMixin,
    SolSeesawFlowRotationStrategy,
):
    pass


def _variant(name: str) -> type:
    return type(name, (FinalSharedSolSeesawStrategy,), {"__module__": __name__})


_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
for _symbol in _SYMBOLS:
    globals()[f"FinalSharedSolSeesaw{_symbol}Strategy"] = _variant(
        f"FinalSharedSolSeesaw{_symbol}Strategy",
    )


WINNER = "strategy_sol_seesaw_rotation:SolSeesawFlowRotationStrategy"
WINNER_TO_FAMILY = {WINNER: "candidate16-sol-seesaw"}


def final_shared_strategy_class_name(winner: str, symbol: str) -> str:
    if winner != WINNER:
        raise ValueError(f"unsupported validated winner: {winner}")
    if symbol not in _SYMBOLS:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"FinalSharedSolSeesaw{symbol}Strategy"


def final_shared_strategy_path(winner: str, symbol: str) -> str:
    return f"{__name__}:{final_shared_strategy_class_name(winner, symbol)}"


def final_shared_strategy_class(winner: str, symbol: str) -> Type:
    return globals()[final_shared_strategy_class_name(winner, symbol)]
