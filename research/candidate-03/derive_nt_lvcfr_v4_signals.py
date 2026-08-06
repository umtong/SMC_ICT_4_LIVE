#!/usr/bin/env python3
"""Route frozen LVCFR events into mutually exclusive causal auction states.

This module does not simulate orders, fills, fees, positions, PnL, or NAV.
It transforms the original, causally confirmed V1 event schedule into a V4
scenario schedule consumed by the existing NautilusTrader Strategy and
BacktestNode.

State priority
--------------
1. VALUE_EDGE_CONTINUATION
   Preserve the successful V3 state unchanged: the event originates in the
   directional outer third of the preceding 240-minute dealing range and the
   first fully completed post-event minute closes in the directional half of
   the event range.
2. EXTERNAL_ACCEPTANCE_CONTINUATION
   The event raids directional external liquidity, three completed minutes
   remain outside the old range, spot aggressive flow supports the direction,
   and deleveraging is moderate rather than an extreme purge.
3. EXTERNAL_RECLAIM_REVERSAL
   The event raids directional external liquidity, then three completed minutes
   close back inside the old range while cumulative price movement opposes the
   event direction.
4. Otherwise NO_TRADE.

The three-minute branches are sequential evidence accumulation, not future
labels: their confirmation time is moved to the close of the third completed
post-event minute. VALUE_EDGE_CONTINUATION retains its one-minute confirmation.
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

from derive_nt_lvcfr_v2_signals import NS_PER_MINUTE, load_futures_minutes, normalize_timestamp_ns


VALUE_EDGE_CONTINUATION = "VALUE_EDGE_CONTINUATION"
EXTERNAL_ACCEPTANCE_CONTINUATION = "EXTERNAL_ACCEPTANCE_CONTINUATION"
EXTERNAL_RECLAIM_REVERSAL = "EXTERNAL_RECLAIM_REVERSAL"


def load_spot_minutes(raw_root: Path) -> dict[int, dict[str, float]]:
    """Load completed spot minutes and causal aggressive quote-flow imbalance."""
    result: dict[int, dict[str, float]] = {}
    for archive_path in sorted((raw_root / "spot_kline").glob("*.zip")):
        with zipfile.ZipFile(archive_path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"expected one CSV in {archive_path}, found {names}")
            with archive.open(names[0]) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
                for row in reader:
                    if not row or not row[0] or not row[0][0].isdigit():
                        continue
                    if len(row) < 11:
                        raise ValueError(f"spot kline row too short in {archive_path}: {row}")
                    timestamp_ns = normalize_timestamp_ns(int(row[0]))
                    minute = timestamp_ns // NS_PER_MINUTE
                    if minute in result:
                        raise ValueError(f"duplicate spot minute {minute}")
                    quote_volume = float(row[7])
                    taker_buy_quote = float(row[10])
                    result[minute] = {
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "flow": (
                            (2.0 * taker_buy_quote - quote_volume) / quote_volume
                            if quote_volume > 0.0
                            else 0.0
                        ),
                    }
    if not result:
        raise ValueError("no spot kline minutes found")
    return result


def _rows(
    values: dict[int, dict[str, float]],
    start: int,
    end: int,
) -> list[dict[str, float]] | None:
    rows = [values.get(minute) for minute in range(start, end)]
    if any(row is None for row in rows):
        return None
    return [row for row in rows if row is not None]


def route_v4_state(
    *,
    origin_alignment: float,
    first_acceptance_fraction: float,
    minimum_origin_alignment: float,
    minimum_acceptance_fraction: float,
    directional_external_swept: bool,
    all_closes_beyond_directional_external: bool,
    all_closes_inside_prior_range: bool,
    directional_spot_flow: float,
    minimum_directional_spot_flow: float,
    total_oi_drop_bp: float,
    maximum_acceptance_oi_drop_bp: float,
    directional_price_change_bp: float,
) -> tuple[str | None, str]:
    """Classify one event using only evidence available at confirmation."""
    if (
        origin_alignment >= minimum_origin_alignment
        and first_acceptance_fraction >= minimum_acceptance_fraction
    ):
        return VALUE_EDGE_CONTINUATION, "VALUE_EDGE_ACCEPTED"
    if (
        directional_external_swept
        and all_closes_beyond_directional_external
        and directional_spot_flow >= minimum_directional_spot_flow
        and total_oi_drop_bp < maximum_acceptance_oi_drop_bp
    ):
        return EXTERNAL_ACCEPTANCE_CONTINUATION, "MODERATE_SPOT_SUPPORTED_EXTERNAL_ACCEPTANCE"
    if (
        directional_external_swept
        and all_closes_inside_prior_range
        and directional_price_change_bp < 0.0
    ):
        return EXTERNAL_RECLAIM_REVERSAL, "EXTERNAL_RAID_RECLAIMED_WITH_OPPOSITE_DISPLACEMENT"

    if not directional_external_swept:
        reason = "NO_DIRECTIONAL_EXTERNAL_LIQUIDITY_RAID"
    elif all_closes_beyond_directional_external:
        if directional_spot_flow < minimum_directional_spot_flow:
            reason = "EXTERNAL_ACCEPTANCE_WITHOUT_SPOT_SUPPORT"
        elif total_oi_drop_bp >= maximum_acceptance_oi_drop_bp:
            reason = "EXTREME_DELEVERAGING_EXHAUSTION_RISK"
        else:
            reason = "UNRESOLVED_EXTERNAL_ACCEPTANCE"
    elif all_closes_inside_prior_range and directional_price_change_bp >= 0.0:
        reason = "RANGE_REENTRY_WITHOUT_OPPOSITE_DISPLACEMENT"
    elif not all_closes_inside_prior_range:
        reason = "MIXED_EXTERNAL_ACCEPTANCE_AND_RECLAIM"
    else:
        reason = "AMBIGUOUS_AUCTION_STATE"
    return None, reason


def derive_v4(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
    dealing_range_minutes: int = 240,
    minimum_origin_alignment: float = 2.0 / 3.0,
    minimum_acceptance_fraction: float = 0.5,
    external_evidence_minutes: int = 3,
    minimum_directional_spot_flow: float = 0.0,
    detector_minimum_total_oi_drop_bp: float = 10.0,
    maximum_external_acceptance_oi_multiple: float = 2.0,
    boundary_stop_buffer_atr: float = 0.20,
    reclaim_stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if dealing_range_minutes <= 0:
        raise ValueError("dealing_range_minutes must be positive")
    if external_evidence_minutes <= 0:
        raise ValueError("external_evidence_minutes must be positive")
    if not 0.5 <= minimum_origin_alignment < 1.0:
        raise ValueError("minimum_origin_alignment must be in [0.5, 1)")
    if not 0.5 <= minimum_acceptance_fraction < 1.0:
        raise ValueError("minimum_acceptance_fraction must be in [0.5, 1)")
    if detector_minimum_total_oi_drop_bp <= 0.0:
        raise ValueError("detector_minimum_total_oi_drop_bp must be positive")
    if maximum_external_acceptance_oi_multiple <= 1.0:
        raise ValueError("maximum_external_acceptance_oi_multiple must exceed 1")

    futures = load_futures_minutes(raw_root)
    spot = load_spot_minutes(raw_root)
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    derived: list[dict[str, Any]] = []
    state_counts = {
        VALUE_EDGE_CONTINUATION: 0,
        EXTERNAL_ACCEPTANCE_CONTINUATION: 0,
        EXTERNAL_RECLAIM_REVERSAL: 0,
        "NO_TRADE": 0,
        "MISSING_CONTEXT": 0,
    }
    no_trade_reasons: dict[str, int] = {}

    maximum_acceptance_oi_drop_bp = (
        detector_minimum_total_oi_drop_bp * maximum_external_acceptance_oi_multiple
    )

    for signal in sorted(source, key=lambda item: int(item["confirm_time_ns"])):
        original_direction = int(signal["direction"])
        original_confirm_ns = int(signal["confirm_time_ns"])
        event_start = int(signal["first_start_time_ns"]) // NS_PER_MINUTE
        event_end = original_confirm_ns // NS_PER_MINUTE
        prior = _rows(futures, event_start - dealing_range_minutes, event_start)
        event = _rows(futures, event_start, event_end)
        post = _rows(futures, event_end, event_end + external_evidence_minutes)
        spot_post = _rows(spot, event_end, event_end + external_evidence_minutes)
        if (
            prior is None
            or len(prior) != dealing_range_minutes
            or event is None
            or len(event) != 10
            or post is None
            or len(post) != external_evidence_minutes
            or spot_post is None
            or len(spot_post) != external_evidence_minutes
        ):
            state_counts["MISSING_CONTEXT"] += 1
            continue

        dealing_low = min(row["low"] for row in prior)
        dealing_high = max(row["high"] for row in prior)
        dealing_span = dealing_high - dealing_low
        event_low = min(row["low"] for row in event)
        event_high = max(row["high"] for row in event)
        event_span = event_high - event_low
        atr = float(signal["atr"])
        if (
            not math.isfinite(dealing_span)
            or dealing_span <= 0.0
            or not math.isfinite(event_span)
            or event_span <= 0.0
            or not math.isfinite(atr)
            or atr <= 0.0
        ):
            state_counts["MISSING_CONTEXT"] += 1
            continue

        event_origin = event[0]["open"]
        origin_position = (event_origin - dealing_low) / dealing_span
        origin_alignment = (
            1.0 - origin_position if original_direction > 0 else origin_position
        )
        first_acceptance_close = post[0]["close"]
        first_acceptance_fraction = (
            (first_acceptance_close - event_low) / event_span
            if original_direction > 0
            else (event_high - first_acceptance_close) / event_span
        )

        directional_external = dealing_high if original_direction > 0 else dealing_low
        directional_external_swept = (
            event_high > dealing_high
            if original_direction > 0
            else event_low < dealing_low
        )
        closes = [row["close"] for row in post]
        all_closes_beyond_directional_external = (
            all(close > dealing_high for close in closes)
            if original_direction > 0
            else all(close < dealing_low for close in closes)
        )
        all_closes_inside_prior_range = all(
            dealing_low < close < dealing_high for close in closes
        )
        directional_spot_flow = sum(
            original_direction * row["flow"] for row in spot_post
        ) / float(external_evidence_minutes)
        directional_price_change_bp = (
            original_direction
            * (post[-1]["close"] - post[0]["open"])
            / post[0]["open"]
            * 10_000.0
        )
        total_oi_drop_bp = float(signal["details"]["total_oi_drop_bp"])

        scenario_kind, no_trade_reason = route_v4_state(
            origin_alignment=origin_alignment,
            first_acceptance_fraction=first_acceptance_fraction,
            minimum_origin_alignment=minimum_origin_alignment,
            minimum_acceptance_fraction=minimum_acceptance_fraction,
            directional_external_swept=directional_external_swept,
            all_closes_beyond_directional_external=all_closes_beyond_directional_external,
            all_closes_inside_prior_range=all_closes_inside_prior_range,
            directional_spot_flow=directional_spot_flow,
            minimum_directional_spot_flow=minimum_directional_spot_flow,
            total_oi_drop_bp=total_oi_drop_bp,
            maximum_acceptance_oi_drop_bp=maximum_acceptance_oi_drop_bp,
            directional_price_change_bp=directional_price_change_bp,
        )
        entry_kind = "CONTINUATION"
        direction = original_direction
        confirm_ns = original_confirm_ns
        stop = float(signal["initial_stop"])

        if scenario_kind == VALUE_EDGE_CONTINUATION:
            confirm_ns = original_confirm_ns + NS_PER_MINUTE
        elif scenario_kind == EXTERNAL_ACCEPTANCE_CONTINUATION:
            confirm_ns = original_confirm_ns + external_evidence_minutes * NS_PER_MINUTE
            stop = directional_external - (
                original_direction * boundary_stop_buffer_atr * atr
            )
        elif scenario_kind == EXTERNAL_RECLAIM_REVERSAL:
            entry_kind = "REVERSAL"
            direction = -original_direction
            confirm_ns = original_confirm_ns + external_evidence_minutes * NS_PER_MINUTE
            stop = (
                event_high + reclaim_stop_buffer_atr * atr
                if original_direction > 0
                else event_low - reclaim_stop_buffer_atr * atr
            )

        common_details = {
            "v1_confirm_time_ns": original_confirm_ns,
            "dealing_range_minutes": dealing_range_minutes,
            "dealing_range_low": dealing_low,
            "dealing_range_high": dealing_high,
            "dealing_range_span": dealing_span,
            "event_origin": event_origin,
            "origin_position": origin_position,
            "origin_alignment": origin_alignment,
            "event_low": event_low,
            "event_high": event_high,
            "event_midpoint": (event_low + event_high) / 2.0,
            "first_acceptance_close": first_acceptance_close,
            "first_acceptance_fraction": first_acceptance_fraction,
            "directional_external": directional_external,
            "directional_external_swept": directional_external_swept,
            "external_evidence_minutes": external_evidence_minutes,
            "post_event_closes": closes,
            "all_closes_beyond_directional_external": all_closes_beyond_directional_external,
            "all_closes_inside_prior_range": all_closes_inside_prior_range,
            "directional_spot_flow": directional_spot_flow,
            "minimum_directional_spot_flow": minimum_directional_spot_flow,
            "directional_price_change_bp": directional_price_change_bp,
            "total_oi_drop_bp": total_oi_drop_bp,
            "maximum_external_acceptance_oi_drop_bp": maximum_acceptance_oi_drop_bp,
        }

        if scenario_kind is None:
            state_counts["NO_TRADE"] += 1
            no_trade_reasons[no_trade_reason] = no_trade_reasons.get(no_trade_reason, 0) + 1
            continue

        details = dict(signal.get("details", {}))
        details.update(common_details)
        details.update(
            {
                "scenario_kind": scenario_kind,
                "entry_kind": entry_kind,
                "original_direction": original_direction,
                "routed_direction": direction,
            }
        )
        item = dict(signal)
        item["scenario_id"] = str(signal["scenario_id"]).replace(
            "NT-LVCFR-", f"NT-LVCFR-V4-{scenario_kind}-"
        )
        item["scenario_kind"] = scenario_kind
        item["entry_kind"] = entry_kind
        item["direction"] = direction
        item["confirm_time_ns"] = confirm_ns
        item["eligible_time_ns"] = confirm_ns
        item["initial_stop"] = stop
        item["details"] = details
        derived.append(item)
        state_counts[scenario_kind] += 1

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(derived, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v4-state-router",
        "engine_status": "causal_state_schedule_only_no_backtest",
        "source_signal_count": len(source),
        "derived_signal_count": len(derived),
        "state_counts": state_counts,
        "no_trade_reasons": no_trade_reasons,
        "state_priority": [
            VALUE_EDGE_CONTINUATION,
            EXTERNAL_ACCEPTANCE_CONTINUATION,
            EXTERNAL_RECLAIM_REVERSAL,
            "NO_TRADE",
        ],
        "dealing_range_minutes": dealing_range_minutes,
        "minimum_origin_alignment": minimum_origin_alignment,
        "minimum_acceptance_fraction": minimum_acceptance_fraction,
        "external_evidence_minutes": external_evidence_minutes,
        "minimum_directional_spot_flow": minimum_directional_spot_flow,
        "detector_minimum_total_oi_drop_bp": detector_minimum_total_oi_drop_bp,
        "maximum_external_acceptance_oi_multiple": maximum_external_acceptance_oi_multiple,
        "maximum_external_acceptance_oi_drop_bp": maximum_acceptance_oi_drop_bp,
        "boundary_stop_buffer_atr": boundary_stop_buffer_atr,
        "reclaim_stop_buffer_atr": reclaim_stop_buffer_atr,
        "source_signals": str(source_signals),
        "output_signals": str(output_signals),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return derived


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--dealing-range-minutes", type=int, default=240)
    parser.add_argument("--minimum-origin-alignment", type=float, default=2.0 / 3.0)
    parser.add_argument("--minimum-acceptance-fraction", type=float, default=0.5)
    parser.add_argument("--external-evidence-minutes", type=int, default=3)
    parser.add_argument("--minimum-directional-spot-flow", type=float, default=0.0)
    parser.add_argument("--detector-minimum-total-oi-drop-bp", type=float, default=10.0)
    parser.add_argument("--maximum-external-acceptance-oi-multiple", type=float, default=2.0)
    parser.add_argument("--boundary-stop-buffer-atr", type=float, default=0.20)
    parser.add_argument("--reclaim-stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()

    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v1.json"
    if not source.exists():
        source = prepared / "signals.json"
    output = prepared / "signals.json"
    derived = derive_v4(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        dealing_range_minutes=args.dealing_range_minutes,
        minimum_origin_alignment=args.minimum_origin_alignment,
        minimum_acceptance_fraction=args.minimum_acceptance_fraction,
        external_evidence_minutes=args.external_evidence_minutes,
        minimum_directional_spot_flow=args.minimum_directional_spot_flow,
        detector_minimum_total_oi_drop_bp=args.detector_minimum_total_oi_drop_bp,
        maximum_external_acceptance_oi_multiple=args.maximum_external_acceptance_oi_multiple,
        boundary_stop_buffer_atr=args.boundary_stop_buffer_atr,
        reclaim_stop_buffer_atr=args.reclaim_stop_buffer_atr,
    )

    manifest = json.loads(args.output_manifest.resolve().read_text(encoding="utf-8"))
    data_manifest_path = prepared / "data_manifest.json"
    if data_manifest_path.exists():
        data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
        data_manifest["candidate"] = "candidate-03-nt-lvcfr-v4-state-router"
        data_manifest["signals"] = len(derived)
        data_manifest["signal_path"] = output.as_posix()
        data_manifest["scenario_transform"] = {
            "type": "mutually_exclusive_auction_state_router",
            "state_priority": manifest["state_priority"],
            "dealing_range_minutes": args.dealing_range_minutes,
            "external_evidence_minutes": args.external_evidence_minutes,
            "minimum_directional_spot_flow": args.minimum_directional_spot_flow,
            "maximum_external_acceptance_oi_drop_bp": manifest[
                "maximum_external_acceptance_oi_drop_bp"
            ],
        }
        data_manifest_path.write_text(
            json.dumps(data_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v4-state-router",
                "source_signals": manifest["source_signal_count"],
                "derived_signals": manifest["derived_signal_count"],
                "state_counts": manifest["state_counts"],
                "no_trade_reasons": manifest["no_trade_reasons"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
