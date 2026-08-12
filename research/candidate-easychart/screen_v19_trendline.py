#!/usr/bin/env python3
"""Run the source-correct active-OB trendline role-flip diagnostic.

This reuses the complete v18 data, target, account and evidence pipeline and
changes only the engine's OB temporal role. Case 02's order block can be a
fresh structure visible to the left of the first retest; it is not required to
have formed after the trendline break.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import screen_v18_trendline as base
from market_v19_trendline import ActiveStructureTrendlineRoleFlipEngine


_base_run = base.run
base.TrendlineRoleFlipEngine = ActiveStructureTrendlineRoleFlipEngine


def run(args: argparse.Namespace) -> dict[str, object]:
    metrics = _base_run(args)
    candidate = "candidate-easychart-v19-active-ob-trendline-role-flip"
    metrics["candidate"] = candidate
    output = args.output.resolve()
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    run_path = output / "run.json"
    record = json.loads(run_path.read_text(encoding="utf-8"))
    record["candidate"] = candidate
    record["engine"] = "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE_V19_ACTIVE_OB"
    record["source_case"] = "02_CxVUB0E9OJU"
    record["source_correction"] = {
        "narrated_ob_location": "visible to the left at the first retest",
        "previous_translation": "post-break OB only",
        "corrected_translation": (
            "fresh active overlapping OB, pre-existing or breakout-response"
        ),
        "unchanged_contracts": [
            "set-membership wick trendline",
            "distinct outside open-close acceptance",
            "first retest only",
            "breakout-wave origin plus OB full invalidation",
            "first active opposing objective",
            "one entry, one stop, one full target, fixed 3 percent NAV risk",
        ],
    }
    record.setdefault("notes", []).append(
        "pre-existing and breakout-response OBs are alternative role witnesses, not votes"
    )
    run_path.write_text(
        json.dumps(record, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False))
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signal-minutes", type=int, default=5)
    parser.add_argument("--response-minutes", type=int, default=5)
    parser.add_argument("--structure-minutes", type=int, default=15)
    parser.add_argument("--dc-atr-period", type=int, default=14)
    parser.add_argument("--dc-atr-multiple", type=float, default=1.0)
    parser.add_argument("--starting-nav", type=float, default=100_000.0)
    parser.add_argument("--warmup-days", type=int, default=35)
    parser.add_argument(
        "--cost-profile",
        choices=("role", "taker", "stress"),
        default="role",
    )
    parser.add_argument("--default-funding-rate", type=float, default=0.0001)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
