#!/usr/bin/env python3
"""Importable one-account wrappers for the v52/v53 residual hypotheses.

This module intentionally does not overwrite the v36 module.  The previous
v52 workflow replaced that module with three aliases and thereby removed
``SystemicRepricingGateMixin``, which prevented the shared runner from importing
before NautilusTrader could start.  These wrappers reuse the already-audited
shared entry lifecycle and final global coordinator while preserving the v52
and v53 market logic unchanged.
"""
from __future__ import annotations

from typing import Type

from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as _shared
from strategy_v52_cross_sectional_residual import CrossSectionalResidualStrategy
from strategy_v53_residual_competition import ResidualStateCompetitionStrategy


# SharedAccountEntryLifecycleMixin resolves this module global at runtime.
# Point it at the strict final coordinator used by the existing shared runner.
_shared.SHARED_ACCOUNT_ENTRY_COORDINATOR = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR


class FinalSharedAccountV52Strategy(
    _shared.SharedAccountEntryLifecycleMixin,
    CrossSectionalResidualStrategy,
):
    """v52 residual convergence with the audited one-slot lifecycle."""


class FinalSharedAccountV53Strategy(
    _shared.SharedAccountEntryLifecycleMixin,
    ResidualStateCompetitionStrategy,
):
    """v53 convergence-versus-catch-up with the audited one-slot lifecycle."""


def _variant(name: str, base: type) -> type:
    return type(
        name,
        (base,),
        {
            "__module__": __name__,
            "__doc__": f"Per-symbol importable {name}; market logic is inherited unchanged.",
        },
    )


_BASES = {
    "v52": FinalSharedAccountV52Strategy,
    "v53": FinalSharedAccountV53Strategy,
}
_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")

for _family, _base in _BASES.items():
    for _symbol in _SYMBOLS:
        _name = f"FinalResidual{_family.upper()}{_symbol}Strategy"
        globals()[_name] = _variant(_name, _base)


WINNER_TO_FAMILY = {
    "strategy_v52_cross_sectional_residual:CrossSectionalResidualStrategy": "v52",
    "strategy_v53_residual_competition:ResidualStateCompetitionStrategy": "v53",
}


def residual_shared_strategy_class_name(winner: str, symbol: str) -> str:
    family = WINNER_TO_FAMILY.get(winner)
    if family is None:
        raise ValueError(f"unsupported residual hypothesis: {winner}")
    if symbol not in _SYMBOLS:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"FinalResidual{family.upper()}{symbol}Strategy"


def residual_shared_strategy_path(winner: str, symbol: str) -> str:
    return f"{__name__}:{residual_shared_strategy_class_name(winner, symbol)}"


def residual_shared_strategy_class(winner: str, symbol: str) -> Type:
    return globals()[residual_shared_strategy_class_name(winner, symbol)]


__all__ = [
    "WINNER_TO_FAMILY",
    "FinalSharedAccountV52Strategy",
    "FinalSharedAccountV53Strategy",
    "residual_shared_strategy_class",
    "residual_shared_strategy_class_name",
    "residual_shared_strategy_path",
    *[
        f"FinalResidual{family.upper()}{symbol}Strategy"
        for family in _BASES
        for symbol in _SYMBOLS
    ],
]
