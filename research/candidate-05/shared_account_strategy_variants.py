#!/usr/bin/env python3
"""Importable per-symbol classes for one shared-account BacktestNode."""
from __future__ import annotations

from typing import Type

from strategy_global_slot_wrappers_v4 import SharedAccountNoEarlySponsoredStrategy
from strategy_global_slot_wrappers_v4 import SharedAccountV26Strategy
from strategy_global_slot_wrappers_v4 import SharedAccountV29bStrategy
from strategy_global_slot_wrappers_v4 import SharedAccountV30Strategy
from strategy_global_slot_wrappers_v4 import SharedAccountV31Strategy
from strategy_global_slot_wrappers_v4 import SharedAccountV32Strategy


def _variant(name: str, base: type) -> type:
    return type(
        name,
        (base,),
        {
            "__module__": __name__,
            "__doc__": f"Shared-account {name} with unchanged parent market logic.",
        },
    )


_BASES = {
    "v26": SharedAccountV26Strategy,
    "no_early": SharedAccountNoEarlySponsoredStrategy,
    "v29b": SharedAccountV29bStrategy,
    "v30": SharedAccountV30Strategy,
    "v31": SharedAccountV31Strategy,
    "v32": SharedAccountV32Strategy,
}

for _family, _base in _BASES.items():
    for _symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
        _name = f"Shared{_family.upper().replace('_', '')}{_symbol}Strategy"
        globals()[_name] = _variant(_name, _base)


WINNER_TO_FAMILY = {
    "strategy_v26:ScenarioValidEntryStrategy": "v26",
    "strategy_v26_no_early_sponsored_ablation:NoEarlySponsoredParticipationStrategy": "no_early",
    "strategy_v29b_external_displacement_fvg:ExternalDisplacementFvgStrategyV2": "v29b",
    "strategy_v30_external_acceptance_retest:ExternalAcceptanceFirstRetestStrategy": "v30",
    "strategy_v31_impact_resiliency_reversal:ImpactResiliencyReversalStrategy": "v31",
    "strategy_v32_queue_pressure_release:QueuePressureReleaseStrategy": "v32",
}


def shared_strategy_class_name(winner: str, symbol: str) -> str:
    family = WINNER_TO_FAMILY.get(winner)
    if family is None:
        raise ValueError(f"unsupported validated winner: {winner}")
    if symbol not in {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"Shared{family.upper().replace('_', '')}{symbol}Strategy"


def shared_strategy_path(winner: str, symbol: str) -> str:
    return f"{__name__}:{shared_strategy_class_name(winner, symbol)}"


def shared_strategy_class(winner: str, symbol: str) -> Type:
    return globals()[shared_strategy_class_name(winner, symbol)]


__all__ = [
    "WINNER_TO_FAMILY",
    "shared_strategy_class",
    "shared_strategy_class_name",
    "shared_strategy_path",
    *[
        f"Shared{family.upper().replace('_', '')}{symbol}Strategy"
        for family in _BASES
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    ],
]
