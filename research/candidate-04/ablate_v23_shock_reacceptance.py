#!/usr/bin/env python3
"""Remove only V23's parent-auction shock-reacceptance branch.

This is a controlled logical ablation. It does not change thresholds, stops,
target selection, risk sizing, costs, or NautilusTrader execution. The input
signals must be the frozen V23 completed-data intents; the sole removed state is
``PARENT_AUCTION_SHOCK_REACCEPTANCE``.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

REMOVED_SCENARIO = "PARENT_AUCTION_SHOCK_REACCEPTANCE"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-signals", type=Path, required=True)
    parser.add_argument("--input-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = json.loads(args.input_signals.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("input signals must be a JSON list")
    summary: dict[str, Any] = json.loads(
        args.input_summary.read_text(encoding="utf-8"),
    )

    kept = [row for row in rows if str(row.get("scenario")) != REMOVED_SCENARIO]
    removed = len(rows) - len(kept)
    previous = -1
    for row in kept:
        timestamp = int(row["observe_time_ns"])
        if timestamp < previous:
            raise RuntimeError("filtered signals are not time ordered")
        previous = timestamp
        if int(row["side"]) not in (-1, 1):
            raise RuntimeError(f"invalid side: {row['side']}")

    output_summary = dict(summary)
    output_summary.update(
        {
            "candidate": "candidate-04-v23a-shock-reacceptance-ablation",
            "ablation": {
                "removed_scenario": REMOVED_SCENARIO,
                "removed_signal_count": removed,
                "unchanged_signal_count": len(kept),
                "changed_variables": 1,
                "unchanged_execution": "NautilusTrader BacktestNode",
            },
            "written_signals": len(kept),
            "unique_signal_bars": len(kept),
        },
    )

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "signals.json").write_text(
        json.dumps(kept, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output / "summary.json").write_text(
        json.dumps(output_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    fieldnames = [
        "scenario",
        "side",
        "signal_index",
        "signal_time",
        "observe_time",
        "observe_time_ns",
        "stop_level",
    ]
    with (args.output / "signals.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in kept:
            writer.writerow({key: row.get(key) for key in fieldnames})

    print(json.dumps(output_summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
