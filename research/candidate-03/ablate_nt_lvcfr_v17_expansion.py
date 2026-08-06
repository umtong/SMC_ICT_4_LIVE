#!/usr/bin/env python3
"""Remove only V17's OI-expansion continuation family from a frozen schedule.

This is the single core-variable ablation pre-specified for a valid V17 logical
failure. It does not alter the retained deleveraging scenarios, their timestamps,
directions, stops, targets, or metadata. It never simulates orders or PnL;
NautilusTrader remains the sole execution and accounting engine.
"""
from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from derive_nt_lvcfr_v17_signals import SPOT_LED_OI_EXPANSION_ACCEPTANCE


def canonical_digest(signals: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        signals,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def ablate_expansion(
    *,
    source_signals: Path,
    output_signals: Path,
    output_manifest: Path,
) -> list[dict[str, Any]]:
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    if not isinstance(source, list):
        raise ValueError("source schedule must be a JSON list")
    retained = [
        signal
        for signal in source
        if str(signal.get("scenario_kind")) != SPOT_LED_OI_EXPANSION_ACCEPTANCE
    ]
    removed = [
        signal
        for signal in source
        if str(signal.get("scenario_kind")) == SPOT_LED_OI_EXPANSION_ACCEPTANCE
    ]
    if not removed:
        raise ValueError("V17 expansion ablation removed no signals")
    if len(retained) + len(removed) != len(source):
        raise RuntimeError("ablation accounting mismatch")

    # Prove that the retained schedules are byte-for-byte equal as JSON values
    # and stay in their original causal order.
    expected = [signal for signal in source if signal not in removed]
    if retained != expected:
        raise RuntimeError("retained non-expansion schedule was modified")
    original_times = [int(signal["confirm_time_ns"]) for signal in retained]
    if original_times != sorted(original_times):
        raise ValueError("retained schedule is not causally ordered")

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(retained, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    state_counts: dict[str, int] = {}
    for signal in retained:
        state = str(signal.get("scenario_kind"))
        state_counts[state] = state_counts.get(state, 0) + 1
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v17-expansion-ablation",
        "experiment_type": "single_core_variable_removal",
        "removed_variable": "SPOT_LED_OI_EXPANSION_ACCEPTANCE_FAMILY",
        "source_signal_count": len(source),
        "removed_signal_count": len(removed),
        "retained_signal_count": len(retained),
        "retained_state_counts": dict(sorted(state_counts.items())),
        "source_schedule_digest": canonical_digest(source),
        "retained_schedule_digest": canonical_digest(retained),
        "source_signals": str(source_signals),
        "output_signals": str(output_signals),
        "invariants": [
            "UNCHANGED_DELEVERAGING_SIGNAL_VALUES",
            "UNCHANGED_CAUSAL_TIMESTAMPS",
            "UNCHANGED_DIRECTIONS_STOPS_TARGETS",
            "UNCHANGED_NAUTILUSTRADER_EXECUTION_AND_ACCOUNTING",
            "UNCHANGED_CURRENT_NAV_RISK_FRACTION_0_03",
        ],
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return retained


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v17-full.json"
    if not source.exists():
        source = prepared / "signals.json"
    output = prepared / "signals.json"
    retained = ablate_expansion(
        source_signals=source,
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
    )
    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v17-expansion-ablation",
                "retained_signals": len(retained),
                "manifest": str(args.output_manifest.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
