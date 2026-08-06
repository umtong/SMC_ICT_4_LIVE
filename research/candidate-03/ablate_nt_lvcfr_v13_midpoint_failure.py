#!/usr/bin/env python3
"""Remove only V13 midpoint-failure reversals.

This is the required one-core-variable ablation after the first development
week showed that a completed close through the event midpoint did not by itself
establish CHoCH. First-break opposite-side reversals and measured-acceptance
continuations remain unchanged, as do all native entry, stop, target, fee,
funding, 3% risk, order, fill, position and NAV rules.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

REMOVED_STATE = "MIDPOINT_FAILURE_CHOCH_REVERSAL"


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
        if str(signal.get("scenario_kind", "")) != REMOVED_STATE
    ]
    removed = len(signals) - len(kept)
    if removed <= 0:
        raise RuntimeError("midpoint-failure ablation removed no signals")

    signals_path.write_text(
        json.dumps(kept, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "ablation": "REMOVE_MIDPOINT_FAILURE_CHOCH_REVERSAL",
        "core_variable": "event_midpoint_close_alone_confirms_failed_break_CHOCH",
        "source_signal_count": len(signals),
        "removed_signal_count": removed,
        "kept_signal_count": len(kept),
        "kept_state_counts": dict(
            sorted(Counter(str(item.get("scenario_kind", "UNKNOWN")) for item in kept).items())
        ),
        "execution_engine": "NautilusTrader native path unchanged",
    }
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
