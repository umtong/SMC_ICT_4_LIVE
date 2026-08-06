#!/usr/bin/env python3
"""Remove same-bar depth-only confirmations from frozen session intents.

The sole changed variable is whether ``SAME_BAR_ATTACK_ABSORPTION`` can confirm
a reversal without observed reversal-side executed flow. Delayed reclaim intents
and all timestamps, stops and market-state details remain unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REMOVED_MODE = "SAME_BAR_ATTACK_ABSORPTION"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-signals", type=Path, required=True)
    parser.add_argument("--input-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = json.loads(args.input_signals.read_text(encoding="utf-8"))
    summary = json.loads(args.input_summary.read_text(encoding="utf-8"))
    kept = [
        row
        for row in rows
        if str((row.get("details") or {}).get("confirmation_mode")) != REMOVED_MODE
    ]
    removed = len(rows) - len(kept)
    previous = -1
    for row in kept:
        timestamp = int(row["observe_time_ns"])
        if timestamp < previous:
            raise RuntimeError("filtered signals are not time ordered")
        previous = timestamp

    output_summary = {
        **summary,
        "candidate": "candidate-04-session-resiliency-delayed-only-ablation",
        "written_signals": len(kept),
        "unique_signal_bars": len(kept),
        "ablation": {
            "removed_confirmation_mode": REMOVED_MODE,
            "removed_signal_count": removed,
            "unchanged_signal_count": len(kept),
            "changed_variables": 1,
            "hypothesis": (
                "displayed-depth replenishment on the attack bar is not a "
                "complete reversal confirmation without reversal-side execution"
            ),
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
