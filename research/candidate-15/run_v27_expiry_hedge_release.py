#!/usr/bin/env python3
"""Run V27 with a schema-preserving empty-result contract.

The frozen expiry state, transition, structural-R and cost rules are unchanged.
This wrapper only ensures that a legitimate zero-candidate outcome remains a
valid tabular result instead of raising during summary generation.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

import diagnose_v27_expiry_hedge_release as v27


_ORIGINAL_CLASSIFY = v27.classify


_EMPTY_COLUMNS = (
    "expiry_date",
    "expiry_ts",
    "symbol",
    "entry_ts",
    "exit_ts",
    "route",
    "direction",
    "direction_sign",
    "entry_price",
    "stop_price",
    "target_price",
    "net_structural_r",
    "rank_score",
    "gross_return",
    "net_return",
    "exit_reason",
    "same_bar_ambiguous",
)


def classify(
    frame: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, Any]:
    candidates, rejected = _ORIGINAL_CLASSIFY(frame, prices, protocol)
    if candidates.empty:
        candidates = pd.DataFrame(columns=_EMPTY_COLUMNS)
    return candidates, rejected


v27.classify = classify


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    v27.execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
