#!/usr/bin/env python3
"""Final importable per-symbol classes for shared-account validation."""
from __future__ import annotations

from typing import Type

from strategy_global_slot_wrappers_v5 import FinalSharedAccountNoEarlySponsoredStrategy
from strategy_global_slot_wrappers_v5 import FinalSharedAccountV26Strategy
from strategy_global_slot_wrappers_v5 import FinalSharedAccountV29bStrategy
from strategy_global_slot_wrappers_v5 import FinalSharedAccountV30Strategy
from strategy_global_slot_wrappers_v5 import FinalSharedAccountV31Strategy
from strategy_global_slot_wrappers_v5 import FinalSharedAccountV32Strategy
from strategy_global_slot_wrappers_v5 import FinalSharedAccountV36Strategy
from strategy_global_slot_wrappers_v5 import FinalSharedAccountV37Strategy


def _variant(name: str, base: type) -> type:
    return type(
        name,
        (base,),
        {
            "__module__": __name__,
            "__doc__": f"Final shared-account {name} with unchanged parent market logic.",
        },
    )


_BASES = {
    "v26": FinalSharedAccountV26Strategy,
    "no_early": FinalSharedAccountNoEarlySponsoredStrategy,
    "v29b": FinalSharedAccountV29bStrategy,
    "v30": FinalSharedAccountV30Strategy,
    "v31": FinalSharedAccountV31Strategy,
    "v32": FinalSharedAccountV32Strategy,
    "v36": FinalSharedAccountV36Strategy,
    "v37": FinalSharedAccountV37Strategy,
}

for _family, _base in _BASES.items():
    for _symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
        _name = f"FinalShared{_family.upper().replace('_', '')}{_symbol}Strategy"
        globals()[_name] = _variant(_name, _base)


WINNER_TO_FAMILY = {
    "strategy_v26:ScenarioValidEntryStrategy": "v26",
    "strategy_v26_no_early_sponsored_ablation:NoEarlySponsoredParticipationStrategy": "no_early",
    "strategy_v29b_external_displacement_fvg:ExternalDisplacementFvgStrategyV2": "v29b",
    "strategy_v30_external_acceptance_retest:ExternalAcceptanceFirstRetestStrategy": "v30",
    "strategy_v31_impact_resiliency_reversal:ImpactResiliencyReversalStrategy": "v31",
    "strategy_v32_queue_pressure_release:QueuePressureReleaseStrategy": "v32",
    "strategy_v36_cross_asset_repricing_gate:SystemicRepricingGateStrategy": "v36",
    "strategy_v37_smt_session_divergence:SmtSessionDivergenceStrategy": "v37",
}


def final_shared_strategy_class_name(winner: str, symbol: str) -> str:
    family = WINNER_TO_FAMILY.get(winner)
    if family is None:
        raise ValueError(f"unsupported validated winner: {winner}")
    if symbol not in {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"FinalShared{family.upper().replace('_', '')}{symbol}Strategy"


def final_shared_strategy_path(winner: str, symbol: str) -> str:
    return f"{__name__}:{final_shared_strategy_class_name(winner, symbol)}"


def final_shared_strategy_class(winner: str, symbol: str) -> Type:
    return globals()[final_shared_strategy_class_name(winner, symbol)]


__all__ = [
    "WINNER_TO_FAMILY",
    "final_shared_strategy_class",
    "final_shared_strategy_class_name",
    "final_shared_strategy_path",
    *[
        f"FinalShared{family.upper().replace('_', '')}{symbol}Strategy"
        for family in _BASES
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    ],
]
