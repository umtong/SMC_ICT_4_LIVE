#!/usr/bin/env python3
"""Importable shared-account classes for the v38 isolated SMT experiment."""
from __future__ import annotations

from typing import Type

from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as _slot
from shared_account_strategy_variants_v2 import final_shared_strategy_path as baseline_shared_strategy_path
from strategy_v38_isolated_smt_reversal import IsolatedSmtReversalStrategy


# The lifecycle mixin resolves this module global at order and position events.
_slot.SHARED_ACCOUNT_ENTRY_COORDINATOR = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR


class SharedAccountV38Strategy(
    _slot.SharedAccountEntryLifecycleMixin,
    IsolatedSmtReversalStrategy,
):
    """v38 market logic behind the existing strict one-account lifecycle."""


def _variant(name: str) -> type:
    return type(
        name,
        (SharedAccountV38Strategy,),
        {
            "__module__": __name__,
            "__doc__": f"Shared-account v38 isolated SMT strategy for {name}.",
        },
    )


WINNER = "strategy_v38_isolated_smt_reversal:IsolatedSmtReversalStrategy"
PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")

for _symbol in PROJECT_SYMBOLS:
    _name = f"SharedV38{_symbol}Strategy"
    globals()[_name] = _variant(_name)


def experimental_shared_strategy_class_name(winner: str, symbol: str) -> str:
    if winner != WINNER:
        raise ValueError(f"unsupported v38 experimental winner: {winner}")
    if symbol not in PROJECT_SYMBOLS:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"SharedV38{symbol}Strategy"


def experimental_shared_strategy_path(winner: str, symbol: str) -> str:
    if winner != WINNER:
        return baseline_shared_strategy_path(winner, symbol)
    return f"{__name__}:{experimental_shared_strategy_class_name(winner, symbol)}"


def experimental_shared_strategy_class(winner: str, symbol: str) -> Type:
    return globals()[experimental_shared_strategy_class_name(winner, symbol)]


__all__ = [
    "PROJECT_SYMBOLS",
    "SharedAccountV38Strategy",
    "WINNER",
    "experimental_shared_strategy_class",
    "experimental_shared_strategy_class_name",
    "experimental_shared_strategy_path",
    *[f"SharedV38{symbol}Strategy" for symbol in PROJECT_SYMBOLS],
]
