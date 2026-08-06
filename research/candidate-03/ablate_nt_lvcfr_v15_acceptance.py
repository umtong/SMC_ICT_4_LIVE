#!/usr/bin/env python3
"""Remove only V15's same-side flow-confirmed acceptance branch.

The V15 first BTC week showed that every executed
FLOW_CONFIRMED_EVENT_ACCEPTANCE episode lost money while the opposite-boundary
CHoCH branch remained positive.  This controlled ablation changes no detector,
reversal state, entry, stop, target, cost, risk, data, or NautilusTrader
execution/accounting rule.  It only removes the one hypothesized continuation
terminal state and writes the retained causal schedule back to disk.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from derive_nt_lvcfr_v15_signals import FLOW_CONFIRMED_EVENT_ACCEPTANCE


def ablate(signals_path: Path, output_manifest: Path) -> list[dict[str, object]]:
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    retained = [
        signal
        for signal in signals
        if str(signal.get("scenario_kind")) != FLOW_CONFIRMED_EVENT_ACCEPTANCE
    ]
    removed = len(signals) - len(retained)
    signals_path.write_text(
        json.dumps(retained, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v15-acceptance-ablation",
        "engine_status": "causal_schedule_ablation_only_no_backtest",
        "source_signal_count": len(signals),
        "removed_state": FLOW_CONFIRMED_EVENT_ACCEPTANCE,
        "removed_signal_count": removed,
        "retained_signal_count": len(retained),
        "retained_state_counts": {},
        "invariants": [
            "SAME_V15_SOURCE_EVENTS",
            "SAME_EVENT_RANGE_CHOCH_REVERSAL",
            "SAME_ENTRIES_STOPS_TARGETS",
            "SAME_COSTS_AND_THREE_PERCENT_NAV_RISK",
            "SAME_NAUTILUS_TRADER_EXECUTION_AND_ACCOUNTING",
            "SAME_FIRST_BTC_WEEK",
        ],
    }
    for signal in retained:
        state = str(signal.get("scenario_kind"))
        counts = manifest["retained_state_counts"]
        counts[state] = counts.get(state, 0) + 1
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return retained


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    retained = ablate(args.signals.resolve(), args.output_manifest.resolve())
    print(json.dumps({"retained_signals": len(retained)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
