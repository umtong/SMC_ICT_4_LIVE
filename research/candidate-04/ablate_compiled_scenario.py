#!/usr/bin/env python3
"""Remove exactly one scenario from frozen causal intents for ablation.

This utility never executes trades or computes PnL. It preserves timestamps,
stops, details and ordering for every remaining intent so the filtered file can
be replayed unchanged through NautilusTrader.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-signals", type=Path, required=True)
    parser.add_argument("--input-summary", type=Path, required=True)
    parser.add_argument("--remove", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = json.loads(args.input_signals.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("input signals must be a JSON list")
    summary: dict[str, Any] = json.loads(
        args.input_summary.read_text(encoding="utf-8"),
    )
    kept = [row for row in rows if str(row.get("scenario")) != args.remove]
    removed = len(rows) - len(kept)

    previous = -1
    for row in kept:
        timestamp = int(row["observe_time_ns"])
        if timestamp < previous:
            raise RuntimeError("filtered signals are not time ordered")
        previous = timestamp
        if int(row["side"]) not in (-1, 1):
            raise RuntimeError(f"invalid signal side: {row['side']}")

    output_summary = {
        **summary,
        "candidate": args.candidate,
        "written_signals": len(kept),
        "unique_signal_bars": len(kept),
        "ablation": {
            "removed_scenario": args.remove,
            "removed_signal_count": removed,
            "unchanged_signal_count": len(kept),
            "changed_variables": 1,
            "execution": "NautilusTrader BacktestNode",
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
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
