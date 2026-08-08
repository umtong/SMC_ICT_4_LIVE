"""Candidate-16 fixed-import shared-account adapter for candidate-05 v47.

Only strategy selection is adapted. NautilusTrader execution, portfolio
accounting, costs, per-symbol configs, and the project-wide one-entry slot are
reused unchanged from the existing candidate-05 shared-account runner.
"""
from __future__ import annotations

from typing import Type

from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as _wrappers
from strategy_v47_relative_value import RelativeValueDislocationStrategy

# The existing lifecycle mixin resolves this module global at runtime. Point it
# at the final release-phase coordinator used by the audited shared runner.
_wrappers.SHARED_ACCOUNT_ENTRY_COORDINATOR = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
SharedAccountEntryLifecycleMixin = _wrappers.SharedAccountEntryLifecycleMixin


class FinalSharedV47Strategy(
    SharedAccountEntryLifecycleMixin,
    RelativeValueDislocationStrategy,
):
    """Unchanged v47 market logic with the existing global-entry lifecycle."""


def _variant(name: str) -> type:
    return type(
        name,
        (FinalSharedV47Strategy,),
        {
            "__module__": __name__,
            "__doc__": f"Fixed v47 shared-account variant for {name}.",
        },
    )


_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
for _symbol in _SYMBOLS:
    globals()[f"FinalSharedV47{_symbol}Strategy"] = _variant(
        f"FinalSharedV47{_symbol}Strategy",
    )


WINNER = "strategy_v47_relative_value:RelativeValueDislocationStrategy"
WINNER_TO_FAMILY = {WINNER: "v47"}


def final_shared_strategy_class_name(winner: str, symbol: str) -> str:
    if winner != WINNER:
        raise ValueError(f"unsupported validated winner: {winner}")
    if symbol not in _SYMBOLS:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"FinalSharedV47{symbol}Strategy"


def final_shared_strategy_path(winner: str, symbol: str) -> str:
    return f"{__name__}:{final_shared_strategy_class_name(winner, symbol)}"


def final_shared_strategy_class(winner: str, symbol: str) -> Type:
    return globals()[final_shared_strategy_class_name(winner, symbol)]


__all__ = [
    "WINNER",
    "WINNER_TO_FAMILY",
    "FinalSharedV47Strategy",
    "final_shared_strategy_class",
    "final_shared_strategy_class_name",
    "final_shared_strategy_path",
    *[f"FinalSharedV47{symbol}Strategy" for symbol in _SYMBOLS],
]
