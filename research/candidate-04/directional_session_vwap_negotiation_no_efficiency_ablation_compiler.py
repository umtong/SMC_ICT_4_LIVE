#!/usr/bin/env python3
"""Single-variable V47 ablation: remove parent-session efficiency cutoff.

The completed parent session must still close beyond one realized VWAP MAD in
its own direction. Counterauction, material OI liquidation, full negotiation
break, untouched parent target, stop and execution are unchanged. Only
``session efficiency > shifted historical median`` is removed.
"""
from __future__ import annotations

from dataclasses import replace
import math

import pandas as pd

import directional_session_vwap_negotiation_compiler as base


_ORIGINAL_CONTEXTS = base.parent_base._directional_contexts


def contexts_without_efficiency_cutoff(
    data: pd.DataFrame,
):
    contexts = _ORIGINAL_CONTEXTS(data)
    result = {}
    for key, state in contexts.items():
        directional = (
            state.side in (-1, 1)
            and math.isfinite(state.vwap)
            and math.isfinite(state.vwap_mad)
            and base.parent_base.directional_value_acceptance(
                state.close,
                state.vwap,
                state.vwap_mad,
                state.side,
            )
        )
        result[key] = replace(state, directional=directional)
    return result


def collect_signals(*args, **kwargs):
    original = base.parent_base._directional_contexts
    base.parent_base._directional_contexts = contexts_without_efficiency_cutoff
    try:
        intents, summary = base.collect_signals(*args, **kwargs)
    finally:
        base.parent_base._directional_contexts = original
    summary = {
        **summary,
        "candidate": "candidate-04-v47b-session-vwap-no-efficiency-ablation",
        "compiler": "candidate-04-session-vwap-negotiation-no-efficiency-ablation",
        "one_variable_ablation": {
            "removed": "parent session efficiency above shifted historical median",
            "retained": [
                "parent close beyond realized VWAP MAD",
                "counter-side completed close flow and return",
                "past-normalized material OI liquidation without rebuild",
                "full prior close-negotiation range break",
                "untouched completed parent-session target",
                "full excursion stop and NautilusTrader execution",
            ],
        },
    }
    return intents, summary


base.v22.collect_signals = collect_signals


if __name__ == "__main__":
    base.v22.main()
