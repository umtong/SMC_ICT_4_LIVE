#!/usr/bin/env python3
"""Remove one compiled two-stage resolution branch for controlled ablation.

This utility does not inspect prices after signal time, execute trades, size risk
or compute PnL. It removes every intent whose already-compiled details identify
one requested resolution branch, preserving all other signal bytes and ordering
for unchanged NautilusTrader replay.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-signals", type=Path, required=True)
    parser.add_argument("--input-summary", type=Path, required=True)
    parser.add_argument("--remove-branch", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = json.loads(args.input_signals.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("input signals must be a JSON list")
    summary = json.loads(args.input_summary.read_text(encoding="utf-8"))

    kept = []
    removed = []
    previous = -1
    for row in rows:
        timestamp = int(row["observe_time_ns"])
        if timestamp < previous:
            raise RuntimeError("input signals are not time ordered")
        previous = timestamp
        details = dict(row.get("details") or {})
        if str(details.get("resolution_branch")) == args.remove_branch:
            removed.append(row)
        else:
            kept.append(row)

    args.output.mkdir(parents=True, exist_ok=True)
    output_summary = {
        **summary,
        "candidate": args.candidate,
        "written_signals": len(kept),
        "unique_signal_bars": len(kept),
        "ablation": {
            "changed_variables": 1,
            "removed_resolution_branch": args.remove_branch,
            "removed_signal_count": len(removed),
            "removed_scenarios": sorted(
                {str(row["scenario"]) for row in removed}
            ),
            "unchanged_signal_count": len(kept),
            "execution": "NautilusTrader BacktestNode",
        },
    }
    (args.output / "signals.json").write_text(
        json.dumps(kept, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output / "summary.json").write_text(
        json.dumps(output_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output_summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
