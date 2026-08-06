#!/usr/bin/env python3
"""Diagnose frozen LVCFR events without simulating orders or selecting PnL rules.

The output separates three questions for every causally confirmed event:

1. Where did the event originate inside the preceding four-hour dealing range?
2. Did the event actually raid directional external liquidity and then reclaim it?
3. What path followed after the first fully observed confirmation minute?

Forward-path columns are diagnostic labels only. They are never written back into
signal schedules and cannot participate in live confirmation.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from derive_nt_lvcfr_v2_signals import NS_PER_MINUTE, load_futures_minutes


def _rows(
    minutes: dict[int, dict[str, float]],
    start: int,
    end: int,
) -> list[dict[str, float]] | None:
    values = [minutes.get(minute) for minute in range(start, end)]
    if any(value is None for value in values):
        return None
    return [value for value in values if value is not None]


def _directional_favorable(
    *,
    direction: int,
    reference: float,
    rows: list[dict[str, float]],
) -> float:
    if direction > 0:
        return max(value["high"] for value in rows) - reference
    return reference - min(value["low"] for value in rows)


def _directional_adverse(
    *,
    direction: int,
    reference: float,
    rows: list[dict[str, float]],
) -> float:
    if direction > 0:
        return reference - min(value["low"] for value in rows)
    return max(value["high"] for value in rows) - reference


def diagnose(
    *,
    prepared_root: Path,
    output_json: Path,
    output_csv: Path,
    dealing_range_minutes: int = 240,
) -> list[dict[str, Any]]:
    minutes = load_futures_minutes(prepared_root / "raw")
    source_path = prepared_root / "signals-v1.json"
    if not source_path.exists():
        source_path = prepared_root / "signals.json"
    signals = json.loads(source_path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []

    for signal in sorted(signals, key=lambda item: int(item["confirm_time_ns"])):
        direction = int(signal["direction"])
        confirm_ns = int(signal["confirm_time_ns"])
        event_start = int(signal["first_start_time_ns"]) // NS_PER_MINUTE
        event_end = confirm_ns // NS_PER_MINUTE
        prior = _rows(
            minutes,
            event_start - dealing_range_minutes,
            event_start,
        )
        event = _rows(minutes, event_start, event_end)
        acceptance = minutes.get(event_end)
        if prior is None or event is None or len(event) != 10 or acceptance is None:
            records.append(
                {
                    "scenario_id": signal["scenario_id"],
                    "confirm_time_ns": confirm_ns,
                    "missing_context": True,
                }
            )
            continue

        dealing_low = min(value["low"] for value in prior)
        dealing_high = max(value["high"] for value in prior)
        dealing_span = dealing_high - dealing_low
        event_origin = event[0]["open"]
        event_low = min(value["low"] for value in event)
        event_high = max(value["high"] for value in event)
        event_midpoint = (event_low + event_high) / 2.0
        atr = float(signal["atr"])
        origin_position = (
            (event_origin - dealing_low) / dealing_span
            if dealing_span > 0
            else math.nan
        )
        origin_alignment = (
            1.0 - origin_position if direction > 0 else origin_position
        )
        directional_external = dealing_high if direction > 0 else dealing_low
        opposite_external = dealing_low if direction > 0 else dealing_high
        directional_sweep = (
            event_high > dealing_high if direction > 0 else event_low < dealing_low
        )
        opposite_sweep = (
            event_low < dealing_low if direction > 0 else event_high > dealing_high
        )
        directional_extension = (
            event_high - dealing_high
            if direction > 0
            else dealing_low - event_low
        )
        acceptance_close = acceptance["close"]
        acceptance_open = acceptance["open"]
        acceptance_back_inside = (
            acceptance_close < dealing_high
            if direction > 0
            else acceptance_close > dealing_low
        )
        acceptance_through_event_midpoint = (
            acceptance_close < event_midpoint
            if direction > 0
            else acceptance_close > event_midpoint
        )
        acceptance_opposite_body = (
            acceptance_close < acceptance_open
            if direction > 0
            else acceptance_close > acceptance_open
        )
        acceptance_original_fraction = (
            (acceptance_close - event_low) / (event_high - event_low)
            if direction > 0 and event_high > event_low
            else (event_high - acceptance_close) / (event_high - event_low)
            if direction < 0 and event_high > event_low
            else math.nan
        )
        continuation_outer_third = origin_alignment >= 2.0 / 3.0
        continuation_acceptance = acceptance_original_fraction >= 0.5
        sweep_reclaim = (
            directional_sweep
            and acceptance_back_inside
            and acceptance_through_event_midpoint
            and acceptance_opposite_body
        )

        record: dict[str, Any] = {
            "scenario_id": signal["scenario_id"],
            "confirm_time_ns": confirm_ns,
            "confirm_time_utc": datetime.fromtimestamp(
                confirm_ns / 1_000_000_000,
                tz=timezone.utc,
            ).isoformat(),
            "utc_hour": datetime.fromtimestamp(
                confirm_ns / 1_000_000_000,
                tz=timezone.utc,
            ).hour,
            "direction": direction,
            "atr": atr,
            "dealing_range_low": dealing_low,
            "dealing_range_high": dealing_high,
            "dealing_range_span_atr": dealing_span / atr if atr > 0 else math.nan,
            "event_origin": event_origin,
            "origin_position": origin_position,
            "origin_alignment": origin_alignment,
            "distance_to_directional_external_atr": abs(
                directional_external - event_origin
            )
            / atr
            if atr > 0
            else math.nan,
            "distance_to_opposite_external_atr": abs(
                opposite_external - event_origin
            )
            / atr
            if atr > 0
            else math.nan,
            "event_low": event_low,
            "event_high": event_high,
            "event_midpoint": event_midpoint,
            "event_span_atr": (event_high - event_low) / atr
            if atr > 0
            else math.nan,
            "directional_external_swept": directional_sweep,
            "opposite_external_swept": opposite_sweep,
            "directional_sweep_extension_atr": directional_extension / atr
            if atr > 0
            else math.nan,
            "acceptance_open": acceptance_open,
            "acceptance_high": acceptance["high"],
            "acceptance_low": acceptance["low"],
            "acceptance_close": acceptance_close,
            "acceptance_original_fraction": acceptance_original_fraction,
            "acceptance_back_inside_prior_range": acceptance_back_inside,
            "acceptance_through_event_midpoint": acceptance_through_event_midpoint,
            "acceptance_opposite_body": acceptance_opposite_body,
            "v3_continuation_outer_third": continuation_outer_third,
            "v3_continuation_acceptance": continuation_acceptance,
            "strict_external_sweep_reclaim": sweep_reclaim,
            "first_displacement_bp": signal["details"][
                "first_displacement_bp"
            ],
            "total_oi_drop_bp": signal["details"]["total_oi_drop_bp"],
            "second_futures_flow": signal["details"]["second_futures_flow"],
            "second_spot_flow": signal["details"]["second_spot_flow"],
            "second_activity_ratio": signal["details"]["second_activity_ratio"],
            "missing_context": False,
        }

        reference = acceptance_close
        for horizon in (5, 15, 30, 60, 180, 240):
            future = _rows(minutes, event_end + 1, event_end + 1 + horizon)
            if future is None or not future:
                record[f"original_mfe_{horizon}m_atr"] = None
                record[f"original_mae_{horizon}m_atr"] = None
                record[f"reversal_mfe_{horizon}m_atr"] = None
                record[f"reversal_mae_{horizon}m_atr"] = None
                continue
            record[f"original_mfe_{horizon}m_atr"] = (
                _directional_favorable(
                    direction=direction,
                    reference=reference,
                    rows=future,
                )
                / atr
                if atr > 0
                else math.nan
            )
            record[f"original_mae_{horizon}m_atr"] = (
                _directional_adverse(
                    direction=direction,
                    reference=reference,
                    rows=future,
                )
                / atr
                if atr > 0
                else math.nan
            )
            record[f"reversal_mfe_{horizon}m_atr"] = (
                _directional_favorable(
                    direction=-direction,
                    reference=reference,
                    rows=future,
                )
                / atr
                if atr > 0
                else math.nan
            )
            record[f"reversal_mae_{horizon}m_atr"] = (
                _directional_adverse(
                    direction=-direction,
                    reference=reference,
                    rows=future,
                )
                / atr
                if atr > 0
                else math.nan
            )
        records.append(record)

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(records, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    fieldnames = sorted({key for record in records for key in record})
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    records = diagnose(
        prepared_root=args.prepared_root.resolve(),
        output_json=args.output_json.resolve(),
        output_csv=args.output_csv.resolve(),
    )
    summary = {
        "events": len(records),
        "v3_continuations": sum(
            bool(record.get("v3_continuation_outer_third"))
            and bool(record.get("v3_continuation_acceptance"))
            for record in records
        ),
        "strict_external_sweep_reclaims": sum(
            bool(record.get("strict_external_sweep_reclaim"))
            for record in records
        ),
        "missing_context": sum(
            bool(record.get("missing_context")) for record in records
        ),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
