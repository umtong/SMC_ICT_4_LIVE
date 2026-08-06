#!/usr/bin/env python3
"""V28 with only balanced-session failed inventory materiality refined."""
from __future__ import annotations

from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v28 as v28
import balanced_session_material_inventory_compiler as material


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
):
    original = v28.v27.balanced_session.collect_signals
    v28.v27.balanced_session.collect_signals = material.collect_signals
    try:
        intents, summary = v28.collect_signals(
            data,
            evaluation_start,
            evaluation_end,
            config,
            impact_parameters,
            router,
        )
    finally:
        v28.v27.balanced_session.collect_signals = original
    return intents, {
        **summary,
        "candidate": "candidate-04-v28-material-balanced-oi",
        "compiler": "candidate-04-v28-material-balanced-oi",
        "controlled_change": (
            "balanced-session failed-inventory route requires state-interval OI "
            "expansion above the shifted median positive OI step only"
        ),
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
