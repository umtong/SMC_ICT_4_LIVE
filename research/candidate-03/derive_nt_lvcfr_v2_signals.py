#!/usr/bin/env python3
"""Derive causal premium/discount + acceptance signals from frozen LVCFR events.

This script does not simulate orders or PnL. It transforms the v1 detector's
causally confirmed OI-contraction events into v2 scenario confirmations:

1. The 10-minute event must originate in the directional outer third of the
   immediately preceding four-hour dealing range (long from discount, short
   from premium).
2. The first completed minute after the v1 event confirmation must still close
   in the directional half of the event range. This is a one-minute acceptance
   confirmation, so the v2 confirmation time is moved to that minute close.

The resulting schedule is consumed by the same NautilusTrader Strategy and
BacktestNode. No order, fill, fee, PnL, or NAV calculation occurs here.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import zipfile
from pathlib import Path
from typing import Any

NS_PER_MINUTE = 60_000_000_000


def normalize_timestamp_ns(raw: int) -> int:
    return raw * (1_000 if raw >= 100_000_000_000_000 else 1_000_000)


def load_futures_minutes(raw_root: Path) -> dict[int, dict[str, float]]:
    result: dict[int, dict[str, float]] = {}
    for archive_path in sorted((raw_root / "futures_kline").glob("*.zip")):
        with zipfile.ZipFile(archive_path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"expected one CSV in {archive_path}, found {names}")
            with archive.open(names[0]) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
                for row in reader:
                    if not row or not row[0] or not row[0][0].isdigit():
                        continue
                    timestamp_ns = normalize_timestamp_ns(int(row[0]))
                    minute = timestamp_ns // NS_PER_MINUTE
                    if minute in result:
                        raise ValueError(f"duplicate minute {minute}")
                    result[minute] = {
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                    }
    if not result:
        raise ValueError("no futures kline minutes found")
    return result


def derive(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
    dealing_range_minutes: int,
    minimum_origin_alignment: float,
    minimum_acceptance_fraction: float,
) -> list[dict[str, Any]]:
    if dealing_range_minutes <= 0:
        raise ValueError("dealing_range_minutes must be positive")
    if not 0.5 <= minimum_origin_alignment < 1.0:
        raise ValueError("minimum_origin_alignment must be in [0.5, 1)")
    if not 0.5 <= minimum_acceptance_fraction < 1.0:
        raise ValueError("minimum_acceptance_fraction must be in [0.5, 1)")

    minutes = load_futures_minutes(raw_root)
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    derived: list[dict[str, Any]] = []
    rejected_location = 0
    rejected_acceptance = 0
    rejected_missing = 0

    for signal in sorted(source, key=lambda item: int(item["confirm_time_ns"])):
        direction = int(signal["direction"])
        original_confirm_ns = int(signal["confirm_time_ns"])
        event_start_minute = int(signal["first_start_time_ns"]) // NS_PER_MINUTE
        event_end_minute = original_confirm_ns // NS_PER_MINUTE

        prior = [minutes.get(value) for value in range(event_start_minute - dealing_range_minutes, event_start_minute)]
        event = [minutes.get(value) for value in range(event_start_minute, event_end_minute)]
        acceptance = minutes.get(event_end_minute)
        if (
            len(prior) != dealing_range_minutes
            or any(value is None for value in prior)
            or len(event) != 10
            or any(value is None for value in event)
            or acceptance is None
        ):
            rejected_missing += 1
            continue
        prior_rows = [value for value in prior if value is not None]
        event_rows = [value for value in event if value is not None]
        dealing_low = min(value["low"] for value in prior_rows)
        dealing_high = max(value["high"] for value in prior_rows)
        span = dealing_high - dealing_low
        if not math.isfinite(span) or span <= 0:
            rejected_missing += 1
            continue
        event_origin = event_rows[0]["open"]
        origin_position = (event_origin - dealing_low) / span
        origin_alignment = (1.0 - origin_position) if direction > 0 else origin_position
        if origin_alignment < minimum_origin_alignment:
            rejected_location += 1
            continue

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
                "dealing_range_minutes": dealing_range_minutes,
                "dealing_range_low": dealing_low,
                "dealing_range_high": dealing_high,
                "event_origin": event_origin,
                "origin_position": origin_position,
                "origin_alignment": origin_alignment,
                "event_low": event_low,
                "event_high": event_high,
                "event_midpoint": (event_low + event_high) / 2.0,
                "acceptance_close": acceptance_close,
                "acceptance_fraction": acceptance_fraction,
                "acceptance_minutes": 1,
            }
        )
        item = dict(signal)
        item["scenario_id"] = str(signal["scenario_id"]).replace("NT-LVCFR-", "NT-LVCFR-V2-")
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
        "candidate": "candidate-03-nt-lvcfr-v2",
        "engine_status": "causal_scenario_schedule_only_no_backtest",
        "source_signal_count": len(source),
        "derived_signal_count": len(derived),
        "rejected_by_origin_location": rejected_location,
        "rejected_by_one_minute_acceptance": rejected_acceptance,
        "rejected_missing_data": rejected_missing,
        "dealing_range_minutes": dealing_range_minutes,
        "minimum_origin_alignment": minimum_origin_alignment,
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
    parser.add_argument("--source-signals", type=Path)
    parser.add_argument("--output-signals", type=Path)
    parser.add_argument("--output-manifest", type=Path)
    parser.add_argument("--dealing-range-minutes", type=int, default=240)
    parser.add_argument("--minimum-origin-alignment", type=float, default=2.0 / 3.0)
    parser.add_argument("--minimum-acceptance-fraction", type=float, default=0.5)
    args = parser.parse_args()

    prepared = args.prepared_root.resolve()
    source = (args.source_signals or (prepared / "signals-v1.json")).resolve()
    if not source.exists():
        fallback = prepared / "signals.json"
        if not fallback.exists():
            parser.error(f"source signals missing: {source} and {fallback}")
        source = fallback
    output = (args.output_signals or (prepared / "signals.json")).resolve()
    manifest = (args.output_manifest or (prepared / "v2_signal_manifest.json")).resolve()
    derived = derive(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=output,
        output_manifest=manifest,
        dealing_range_minutes=args.dealing_range_minutes,
        minimum_origin_alignment=args.minimum_origin_alignment,
        minimum_acceptance_fraction=args.minimum_acceptance_fraction,
    )
    print(json.dumps({"derived_signals": len(derived), "signals_path": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
