#!/usr/bin/env python3
"""V27 with only the failed-break parent-impact boundary refined."""
from __future__ import annotations

from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v27 as v27
import failed_external_break_retest_impact_tail_compiler as refinement


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
):
    original = v27.failed_break.collect_signals
    v27.failed_break.collect_signals = refinement.collect_signals
    try:
        intents, summary = v27.collect_signals(
            data,
            evaluation_start,
            evaluation_end,
            config,
            impact_parameters,
            router,
        )
    finally:
        v27.failed_break.collect_signals = original
    return intents, {
        **summary,
        "candidate": "candidate-04-v27-impact-tail",
        "compiler": "candidate-04-v27-impact-tail",
        "controlled_change": (
            "failed-break liquidation route requires a shifted past-only "
            "parent-impact tail; all other V27 mechanisms are unchanged"
        ),
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
