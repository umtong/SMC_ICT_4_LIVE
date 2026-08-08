#!/usr/bin/env python3
"""Safe empty-slot sentinel repair for the frozen v10 Open-Drive study.

The base study used pandas' minimum representable timestamp as a synthetic
pre-history slot boundary.  Subtraction near nanosecond limits can overflow.
This launcher represents an empty global slot with ``None`` and changes no
session, state, transition, entry, stop, target, cost, outcome or promotion
rule.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import v10_open_drive_study as base


def safe_enforce_one_global_slot(
    candidates: list[base.CandidateTrade],
    panels: dict[str, pd.DataFrame],
) -> tuple[list[base.ScoredTrade], int]:
    scored: list[base.ScoredTrade] = []
    active_until: pd.Timestamp | None = None
    conflicts = 0
    for candidate in sorted(candidates, key=lambda item: item.entry_ts):
        if active_until is not None and candidate.entry_ts <= active_until:
            conflicts += 1
            continue
        result = base.score_candidate(candidate, panels[candidate.symbol])
        if result is None:
            continue
        scored.append(result)
        active_until = result.exit_ts
    return scored, conflicts


base.enforce_one_global_slot = safe_enforce_one_global_slot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = base.run(args.cache.resolve(), args.output.resolve())
    result["empty_global_slot_sentinel"] = {
        "representation": "None until first accepted candidate",
        "economic_logic_changed": False,
    }
    base.write_json(args.output / "study.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
