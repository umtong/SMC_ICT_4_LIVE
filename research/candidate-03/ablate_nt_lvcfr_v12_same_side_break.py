#!/usr/bin/env python3
"""Remove only same-side event-range breaks from a V12 schedule.

This is the required one-core-variable ablation after V12 failed its first BTC
development week. It tests the assumption that the first completed post-event
break in the original liquidation direction is a tradable BOS continuation.
All opposite-side CHoCH/failure-reversal signals and every downstream entry,
stop, target, fee, funding, 3% risk, order, fill, position and NAV rule remain
unchanged. NautilusTrader remains the sole execution/accounting engine.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

CONTINUATION_STATES = {
    "INTERNAL_EVENT_RANGE_BOS_CONTINUATION",
    "EXTERNAL_EVENT_RANGE_EXPANSION_CONTINUATION",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()

    signals_path = args.prepared_root / "signals.json"
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    kept = [
        signal
        for signal in signals
        if str(signal.get("scenario_kind", "")) not in CONTINUATION_STATES
    ]
    removed = len(signals) - len(kept)
    if removed <= 0:
        raise RuntimeError("same-side break ablation removed no signals")
    if any(str(signal.get("entry_kind", "")) != "REVERSAL" for signal in kept):
        raise RuntimeError("ablation retained a non-reversal signal")

    signals_path.write_text(
        json.dumps(kept, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "ablation": "REMOVE_SAME_SIDE_EVENT_RANGE_BREAK_CONTINUATIONS",
        "core_variable": "same_direction_first_completed_break_is_BOS_continuation",
        "source_signal_count": len(signals),
        "removed_signal_count": removed,
        "kept_signal_count": len(kept),
        "kept_state_counts": dict(
            sorted(Counter(str(item.get("scenario_kind", "UNKNOWN")) for item in kept).items())
        ),
        "execution_engine": "NautilusTrader native path unchanged",
    }
    args.output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
