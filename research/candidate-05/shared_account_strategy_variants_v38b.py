#!/usr/bin/env python3
"""Importable shared-account classes for the v38b reachability ablation."""
from __future__ import annotations

from typing import Type

from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as _slot
from shared_account_strategy_variants_v2 import final_shared_strategy_path as baseline_shared_strategy_path
from strategy_v38b_reachable_isolated_smt import ReachableIsolatedSmtReversalStrategy


_slot.SHARED_ACCOUNT_ENTRY_COORDINATOR = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR


class SharedAccountV38bStrategy(
    _slot.SharedAccountEntryLifecycleMixin,
    ReachableIsolatedSmtReversalStrategy,
):
    """v38b market logic behind the strict one-account lifecycle."""


def _variant(name: str) -> type:
    return type(
        name,
        (SharedAccountV38bStrategy,),
        {
            "__module__": __name__,
            "__doc__": f"Shared-account v38b reachability strategy for {name}.",
        },
    )


WINNER = (
    "strategy_v38b_reachable_isolated_smt:"
    "ReachableIsolatedSmtReversalStrategy"
)
PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")

for _symbol in PROJECT_SYMBOLS:
    _name = f"SharedV38B{_symbol}Strategy"
    globals()[_name] = _variant(_name)


def experimental_shared_strategy_class_name(winner: str, symbol: str) -> str:
    if winner != WINNER:
        raise ValueError(f"unsupported v38b experimental winner: {winner}")
    if symbol not in PROJECT_SYMBOLS:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"SharedV38B{symbol}Strategy"


def experimental_shared_strategy_path(winner: str, symbol: str) -> str:
    if winner != WINNER:
        return baseline_shared_strategy_path(winner, symbol)
    return f"{__name__}:{experimental_shared_strategy_class_name(winner, symbol)}"


def experimental_shared_strategy_class(winner: str, symbol: str) -> Type:
    return globals()[experimental_shared_strategy_class_name(winner, symbol)]


__all__ = [
    "PROJECT_SYMBOLS",
    "SharedAccountV38bStrategy",
    "WINNER",
    "experimental_shared_strategy_class",
    "experimental_shared_strategy_class_name",
    "experimental_shared_strategy_path",
    *[f"SharedV38B{symbol}Strategy" for symbol in PROJECT_SYMBOLS],
]
