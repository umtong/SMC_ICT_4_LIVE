"""Candidate-16 shared-account adapter for quarter-hour inventory transfer."""
from __future__ import annotations

from typing import Type

from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as _wrappers
from strategy_quarter_hour_inventory_transfer import QuarterHourInventoryTransferStrategy

_wrappers.SHARED_ACCOUNT_ENTRY_COORDINATOR = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
SharedAccountEntryLifecycleMixin = _wrappers.SharedAccountEntryLifecycleMixin


class FinalSharedQuarterHourInventoryTransferStrategy(
    SharedAccountEntryLifecycleMixin,
    QuarterHourInventoryTransferStrategy,
):
    """Common market logic with the audited project-wide one-entry lifecycle."""


def _variant(name: str) -> type:
    return type(name, (FinalSharedQuarterHourInventoryTransferStrategy,), {"__module__": __name__})


_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
for _symbol in _SYMBOLS:
    globals()[f"FinalSharedQHIT{_symbol}Strategy"] = _variant(
        f"FinalSharedQHIT{_symbol}Strategy",
    )

WINNER = (
    "strategy_quarter_hour_inventory_transfer:"
    "QuarterHourInventoryTransferStrategy"
)
WINNER_TO_FAMILY = {WINNER: "qhit"}


def final_shared_strategy_class_name(winner: str, symbol: str) -> str:
    if winner != WINNER:
        raise ValueError(f"unsupported validated winner: {winner}")
    if symbol not in _SYMBOLS:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"FinalSharedQHIT{symbol}Strategy"


def final_shared_strategy_path(winner: str, symbol: str) -> str:
    return f"{__name__}:{final_shared_strategy_class_name(winner, symbol)}"


def final_shared_strategy_class(winner: str, symbol: str) -> Type:
    return globals()[final_shared_strategy_class_name(winner, symbol)]


__all__ = [
    "WINNER",
    "WINNER_TO_FAMILY",
    "FinalSharedQuarterHourInventoryTransferStrategy",
    "final_shared_strategy_class",
    "final_shared_strategy_class_name",
    "final_shared_strategy_path",
    *[f"FinalSharedQHIT{symbol}Strategy" for symbol in _SYMBOLS],
]
