#!/usr/bin/env python3
"""V28 with only q95 parent impact replaced by q99 extreme impact."""
from __future__ import annotations

from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v28 as v28
import failed_external_break_retest_extreme_tail_compiler as extreme


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
):
    original = v28.impact_tail.collect_signals
    v28.impact_tail.collect_signals = extreme.collect_signals
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
        v28.impact_tail.collect_signals = original
    return intents, {
        **summary,
        "candidate": "candidate-04-v28-extreme-break",
        "compiler": "candidate-04-v28-extreme-break",
        "controlled_change": (
            "failed-break parent-impact boundary changed from shifted q95 to "
            "shifted q99 only"
        ),
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
