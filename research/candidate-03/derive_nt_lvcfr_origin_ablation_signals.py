#!/usr/bin/env python3
"""Ablate only V3's directional outer-third origin condition.

The frozen V1 event detector is unchanged. The first fully completed minute
following the V1 confirmation must still close in the event direction's half
of the ten-minute event range. This script removes only the four-hour
premium/discount origin gate so its causal contribution can be isolated.

It produces a signal schedule only. NautilusTrader remains responsible for
orders, fills, fees, funding, positions, account balances, and NAV.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from derive_nt_lvcfr_v2_signals import NS_PER_MINUTE, load_futures_minutes


def derive_origin_ablation(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
    minimum_acceptance_fraction: float = 0.5,
) -> list[dict[str, Any]]:
    if not 0.5 <= minimum_acceptance_fraction < 1.0:
        raise ValueError("minimum_acceptance_fraction must be in [0.5, 1)")

    minutes = load_futures_minutes(raw_root)
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    derived: list[dict[str, Any]] = []
    rejected_acceptance = 0
    rejected_missing = 0

    for signal in sorted(source, key=lambda item: int(item["confirm_time_ns"])):
        direction = int(signal["direction"])
        original_confirm_ns = int(signal["confirm_time_ns"])
        event_start_minute = int(signal["first_start_time_ns"]) // NS_PER_MINUTE
        event_end_minute = original_confirm_ns // NS_PER_MINUTE
        event = [minutes.get(value) for value in range(event_start_minute, event_end_minute)]
        acceptance = minutes.get(event_end_minute)
        if len(event) != 10 or any(value is None for value in event) or acceptance is None:
            rejected_missing += 1
            continue

        event_rows = [value for value in event if value is not None]
        event_low = min(value["low"] for value in event_rows)
        event_high = max(value["high"] for value in event_rows)
        event_span = event_high - event_low
        if not math.isfinite(event_span) or event_span <= 0:
            rejected_missing += 1
            continue
        acceptance_close = acceptance["close"]
        acceptance_fraction = (
            (acceptance_close - event_low) / event_span
            if direction > 0
            else (event_high - acceptance_close) / event_span
        )
        if acceptance_fraction < minimum_acceptance_fraction:
            rejected_acceptance += 1
            continue

        accepted_ns = original_confirm_ns + NS_PER_MINUTE
        details = dict(signal.get("details", {}))
        details.update(
            {
                "v1_confirm_time_ns": original_confirm_ns,
                "event_low": event_low,
                "event_high": event_high,
                "event_midpoint": (event_low + event_high) / 2.0,
                "acceptance_close": acceptance_close,
                "acceptance_fraction": acceptance_fraction,
                "acceptance_minutes": 1,
                "ablation": "REMOVED_DIRECTIONAL_OUTER_THIRD_ORIGIN_ONLY",
            }
        )
        item = dict(signal)
        item["scenario_id"] = str(signal["scenario_id"]).replace(
            "NT-LVCFR-", "NT-LVCFR-V3-ABLATE-ORIGIN-"
        )
        item["confirm_time_ns"] = accepted_ns
        item["eligible_time_ns"] = accepted_ns
        item["details"] = details
        derived.append(item)

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(derived, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v3-ablate-origin",
        "engine_status": "causal_ablation_schedule_only_no_backtest",
        "source_signal_count": len(source),
        "derived_signal_count": len(derived),
        "removed_core_variable": "directional_outer_third_origin_in_preceding_240m_dealing_range",
        "retained_core_variable": "first_completed_minute_directional_event_half_acceptance",
        "rejected_by_one_minute_acceptance": rejected_acceptance,
        "rejected_missing_data": rejected_missing,
        "minimum_acceptance_fraction": minimum_acceptance_fraction,
        "source_signals": str(source_signals),
        "output_signals": str(output_signals),
    }
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return derived


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--minimum-acceptance-fraction", type=float, default=0.5)
    args = parser.parse_args()

    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v1.json"
    if not source.exists():
        source = prepared / "signals.json"
    output = prepared / "signals.json"
    signals = derive_origin_ablation(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        minimum_acceptance_fraction=args.minimum_acceptance_fraction,
    )

    data_manifest_path = prepared / "data_manifest.json"
    if data_manifest_path.exists():
        data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
        data_manifest["candidate"] = "candidate-03-nt-lvcfr-v3-ablate-origin"
        data_manifest["signals"] = len(signals)
        data_manifest["signal_path"] = output.as_posix()
        data_manifest["scenario_transform"] = {
            "ablation": "removed directional outer-third origin only",
            "acceptance_minutes": 1,
            "minimum_directional_event_range_fraction": args.minimum_acceptance_fraction,
        }
        data_manifest_path.write_text(
            json.dumps(data_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v3-ablate-origin",
                "derived_signals": len(signals),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
