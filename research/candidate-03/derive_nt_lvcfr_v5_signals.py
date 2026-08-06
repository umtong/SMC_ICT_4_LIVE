#!/usr/bin/env python3
"""Route frozen LVCFR events with dealing-range migration and reclaim states.

V5 preserves V4's externally confirmed states and the successful V3 value-edge
state. It changes only the subset where a liquidation impulse points against a
strongly migrating 240-minute dealing range:

- If the prior range traversed at least two thirds in the opposite direction,
  the impulse is treated as a counter-trend liquidation rather than a valid
  value-edge continuation.
- The strategy waits, for at most half of the dealing-range horizon (120 fully
  completed minutes), for price to close back through the ten-minute event
  midpoint in the prior migration direction.
- On reclaim it emits a reversal signal with a stop beyond every extreme
  observed up to confirmation. Without reclaim it emits NO_TRADE.

This module produces a causal signal schedule only. NautilusTrader remains
responsible for orders, fills, fees, funding, margin, positions, and NAV.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from derive_nt_lvcfr_v2_signals import NS_PER_MINUTE, load_futures_minutes
from derive_nt_lvcfr_v4_signals import (
    EXTERNAL_ACCEPTANCE_CONTINUATION,
    EXTERNAL_RECLAIM_REVERSAL,
    VALUE_EDGE_CONTINUATION,
    load_spot_minutes,
    route_v4_state,
)

RANGE_MIGRATION_RECLAIM_REVERSAL = "RANGE_MIGRATION_RECLAIM_REVERSAL"


def _rows(
    values: dict[int, dict[str, float]],
    start: int,
    end: int,
) -> list[dict[str, float]] | None:
    rows = [values.get(minute) for minute in range(start, end)]
    if any(row is None for row in rows):
        return None
    return [row for row in rows if row is not None]


def opposite_range_migration_fraction(
    prior: list[dict[str, float]],
    *,
    event_direction: int,
    dealing_range_span: float,
) -> float:
    """Return prior range traversal opposite the event, normalized by span."""
    if event_direction not in {-1, 1}:
        raise ValueError("event_direction must be -1 or 1")
    if not math.isfinite(dealing_range_span) or dealing_range_span <= 0.0:
        raise ValueError("dealing_range_span must be positive")
    directional_traversal = (
        event_direction
        * (prior[-1]["close"] - prior[0]["open"])
        / dealing_range_span
    )
    return max(0.0, -directional_traversal)


def find_migration_reclaim(
    futures: dict[int, dict[str, float]],
    *,
    event_end_minute: int,
    event_midpoint: float,
    migration_direction: int,
    expiry_minutes: int,
) -> tuple[int, list[dict[str, float]]] | None:
    """Find the first completed minute reclaiming the event midpoint."""
    if migration_direction not in {-1, 1}:
        raise ValueError("migration_direction must be -1 or 1")
    if expiry_minutes <= 0:
        raise ValueError("expiry_minutes must be positive")
    observed: list[dict[str, float]] = []
    for offset in range(expiry_minutes):
        row = futures.get(event_end_minute + offset)
        if row is None:
            return None
        observed.append(row)
        if migration_direction * (row["close"] - event_midpoint) > 0.0:
            return offset + 1, observed
    return None


def derive_v5(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
    dealing_range_minutes: int = 240,
    minimum_origin_alignment: float = 2.0 / 3.0,
    minimum_acceptance_fraction: float = 0.5,
    minimum_opposite_migration_fraction: float = 2.0 / 3.0,
    migration_reclaim_expiry_minutes: int = 120,
    external_evidence_minutes: int = 3,
    minimum_directional_spot_flow: float = 0.0,
    detector_minimum_total_oi_drop_bp: float = 10.0,
    maximum_external_acceptance_oi_multiple: float = 2.0,
    boundary_stop_buffer_atr: float = 0.20,
    reversal_stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if dealing_range_minutes <= 0:
        raise ValueError("dealing_range_minutes must be positive")
    if migration_reclaim_expiry_minutes <= 0:
        raise ValueError("migration_reclaim_expiry_minutes must be positive")
    if not 0.5 <= minimum_opposite_migration_fraction < 1.0:
        raise ValueError("minimum_opposite_migration_fraction must be in [0.5, 1)")

    futures = load_futures_minutes(raw_root)
    spot = load_spot_minutes(raw_root)
    source = json.loads(source_signals.read_text(encoding="utf-8"))
    derived: list[dict[str, Any]] = []
    state_counts = {
        VALUE_EDGE_CONTINUATION: 0,
        RANGE_MIGRATION_RECLAIM_REVERSAL: 0,
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
        event_midpoint = (event_low + event_high) / 2.0
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
        opposite_migration = opposite_range_migration_fraction(
            prior,
            event_direction=original_direction,
            dealing_range_span=dealing_span,
        )

        directional_external = dealing_high if original_direction > 0 else dealing_low
        directional_external_swept = (
            event_high > dealing_high
            if original_direction > 0
            else event_low < dealing_low
        )
        post_closes = [row["close"] for row in post]
        all_closes_beyond_directional_external = (
            all(close > dealing_high for close in post_closes)
            if original_direction > 0
            else all(close < dealing_low for close in post_closes)
        )
        all_closes_inside_prior_range = all(
            dealing_low < close < dealing_high for close in post_closes
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

        scenario_kind: str | None = None
        entry_kind = "CONTINUATION"
        direction = original_direction
        confirm_ns = original_confirm_ns
        stop = float(signal["initial_stop"])
        no_trade_reason = ""
        extra: dict[str, Any] = {}

        value_edge = (
            origin_alignment >= minimum_origin_alignment
            and first_acceptance_fraction >= minimum_acceptance_fraction
        )
        if value_edge:
            if opposite_migration >= minimum_opposite_migration_fraction:
                migration_direction = -original_direction
                reclaim = find_migration_reclaim(
                    futures,
                    event_end_minute=event_end,
                    event_midpoint=event_midpoint,
                    migration_direction=migration_direction,
                    expiry_minutes=migration_reclaim_expiry_minutes,
                )
                if reclaim is not None:
                    reclaim_minutes, observed_after_event = reclaim
                    observed = list(event) + observed_after_event
                    observed_low = min(row["low"] for row in observed)
                    observed_high = max(row["high"] for row in observed)
                    scenario_kind = RANGE_MIGRATION_RECLAIM_REVERSAL
                    entry_kind = "REVERSAL"
                    direction = migration_direction
                    confirm_ns = original_confirm_ns + reclaim_minutes * NS_PER_MINUTE
                    stop = (
                        observed_low - reversal_stop_buffer_atr * atr
                        if direction > 0
                        else observed_high + reversal_stop_buffer_atr * atr
                    )
                    extra.update(
                        {
                            "migration_direction": migration_direction,
                            "migration_reclaim_minutes": reclaim_minutes,
                            "migration_reclaim_close": observed_after_event[-1]["close"],
                            "observed_low_through_reclaim": observed_low,
                            "observed_high_through_reclaim": observed_high,
                        }
                    )
                else:
                    no_trade_reason = "STRONG_OPPOSITE_RANGE_MIGRATION_WITHOUT_MIDPOINT_RECLAIM"
            else:
                scenario_kind = VALUE_EDGE_CONTINUATION
                confirm_ns = original_confirm_ns + NS_PER_MINUTE
        else:
            v4_kind, no_trade_reason = route_v4_state(
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
            scenario_kind = v4_kind
            if scenario_kind == EXTERNAL_ACCEPTANCE_CONTINUATION:
                confirm_ns = original_confirm_ns + external_evidence_minutes * NS_PER_MINUTE
                stop = directional_external - (
                    original_direction * boundary_stop_buffer_atr * atr
                )
            elif scenario_kind == EXTERNAL_RECLAIM_REVERSAL:
                entry_kind = "REVERSAL"
                direction = -original_direction
                confirm_ns = original_confirm_ns + external_evidence_minutes * NS_PER_MINUTE
                stop = (
                    event_high + reversal_stop_buffer_atr * atr
                    if original_direction > 0
                    else event_low - reversal_stop_buffer_atr * atr
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
            "event_midpoint": event_midpoint,
            "first_acceptance_close": first_acceptance_close,
            "first_acceptance_fraction": first_acceptance_fraction,
            "opposite_range_migration_fraction": opposite_migration,
            "minimum_opposite_migration_fraction": minimum_opposite_migration_fraction,
            "migration_reclaim_expiry_minutes": migration_reclaim_expiry_minutes,
            "directional_external": directional_external,
            "directional_external_swept": directional_external_swept,
            "external_evidence_minutes": external_evidence_minutes,
            "post_event_closes": post_closes,
            "all_closes_beyond_directional_external": all_closes_beyond_directional_external,
            "all_closes_inside_prior_range": all_closes_inside_prior_range,
            "directional_spot_flow": directional_spot_flow,
            "minimum_directional_spot_flow": minimum_directional_spot_flow,
            "directional_price_change_bp": directional_price_change_bp,
            "total_oi_drop_bp": total_oi_drop_bp,
            "maximum_external_acceptance_oi_drop_bp": maximum_acceptance_oi_drop_bp,
            **extra,
        }

        if scenario_kind is None:
            state_counts["NO_TRADE"] += 1
            reason = no_trade_reason or "AMBIGUOUS_AUCTION_STATE"
            no_trade_reasons[reason] = no_trade_reasons.get(reason, 0) + 1
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
            "NT-LVCFR-", f"NT-LVCFR-V5-{scenario_kind}-"
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
        "candidate": "candidate-03-nt-lvcfr-v5-migration-reclaim",
        "engine_status": "causal_state_schedule_only_no_backtest",
        "source_signal_count": len(source),
        "derived_signal_count": len(derived),
        "state_counts": state_counts,
        "no_trade_reasons": no_trade_reasons,
        "state_priority": [
            "VALUE_EDGE_OR_MIGRATION_RECLAIM",
            EXTERNAL_ACCEPTANCE_CONTINUATION,
            EXTERNAL_RECLAIM_REVERSAL,
            "NO_TRADE",
        ],
        "dealing_range_minutes": dealing_range_minutes,
        "minimum_origin_alignment": minimum_origin_alignment,
        "minimum_acceptance_fraction": minimum_acceptance_fraction,
        "minimum_opposite_migration_fraction": minimum_opposite_migration_fraction,
        "migration_reclaim_expiry_minutes": migration_reclaim_expiry_minutes,
        "external_evidence_minutes": external_evidence_minutes,
        "minimum_directional_spot_flow": minimum_directional_spot_flow,
        "maximum_external_acceptance_oi_drop_bp": maximum_acceptance_oi_drop_bp,
        "boundary_stop_buffer_atr": boundary_stop_buffer_atr,
        "reversal_stop_buffer_atr": reversal_stop_buffer_atr,
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
    parser.add_argument("--minimum-opposite-migration-fraction", type=float, default=2.0 / 3.0)
    parser.add_argument("--migration-reclaim-expiry-minutes", type=int, default=120)
    parser.add_argument("--external-evidence-minutes", type=int, default=3)
    parser.add_argument("--minimum-directional-spot-flow", type=float, default=0.0)
    parser.add_argument("--detector-minimum-total-oi-drop-bp", type=float, default=10.0)
    parser.add_argument("--maximum-external-acceptance-oi-multiple", type=float, default=2.0)
    parser.add_argument("--boundary-stop-buffer-atr", type=float, default=0.20)
    parser.add_argument("--reversal-stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()

    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v1.json"
    if not source.exists():
        source = prepared / "signals.json"
    output = prepared / "signals.json"
    derived = derive_v5(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        dealing_range_minutes=args.dealing_range_minutes,
        minimum_origin_alignment=args.minimum_origin_alignment,
        minimum_acceptance_fraction=args.minimum_acceptance_fraction,
        minimum_opposite_migration_fraction=args.minimum_opposite_migration_fraction,
        migration_reclaim_expiry_minutes=args.migration_reclaim_expiry_minutes,
        external_evidence_minutes=args.external_evidence_minutes,
        minimum_directional_spot_flow=args.minimum_directional_spot_flow,
        detector_minimum_total_oi_drop_bp=args.detector_minimum_total_oi_drop_bp,
        maximum_external_acceptance_oi_multiple=args.maximum_external_acceptance_oi_multiple,
        boundary_stop_buffer_atr=args.boundary_stop_buffer_atr,
        reversal_stop_buffer_atr=args.reversal_stop_buffer_atr,
    )

    manifest = json.loads(args.output_manifest.resolve().read_text(encoding="utf-8"))
    data_manifest_path = prepared / "data_manifest.json"
    if data_manifest_path.exists():
        data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
        data_manifest["candidate"] = "candidate-03-nt-lvcfr-v5-migration-reclaim"
        data_manifest["signals"] = len(derived)
        data_manifest["signal_path"] = output.as_posix()
        data_manifest["scenario_transform"] = {
            "type": "dealing_range_migration_and_reclaim_router",
            "state_priority": manifest["state_priority"],
            "dealing_range_minutes": args.dealing_range_minutes,
            "minimum_opposite_migration_fraction": args.minimum_opposite_migration_fraction,
            "migration_reclaim_expiry_minutes": args.migration_reclaim_expiry_minutes,
            "external_evidence_minutes": args.external_evidence_minutes,
        }
        data_manifest_path.write_text(
            json.dumps(data_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v5-migration-reclaim",
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
