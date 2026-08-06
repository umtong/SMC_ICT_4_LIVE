#!/usr/bin/env python3
"""Remove only VALUE_EDGE_CONTINUATION from a frozen V11 signal schedule.

This is the single core-variable ablation required after V11 failed its third
frozen BTC week.  It does not alter detection thresholds, the remaining state
logic, entries, stops, targets, risk sizing, fees, funding, execution, positions,
or NAV accounting.  NautilusTrader remains the sole execution/accounting path.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


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
        if str(signal.get("scenario_kind", "")) != "VALUE_EDGE_CONTINUATION"
    ]
    removed = len(signals) - len(kept)
    if removed <= 0:
        raise RuntimeError("VALUE_EDGE_CONTINUATION ablation removed no signals")

    signals_path.write_text(
        json.dumps(kept, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "ablation": "REMOVE_VALUE_EDGE_CONTINUATION",
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
