#!/usr/bin/env python3
"""Build V7 sequential reclaim/reacceptance states and protection objectives.

V7 keeps the frozen V5 event detector, the 240-minute dealing-range migration
router, all stops, risk budgeting, costs, and NautilusTrader execution. It
changes only post-event state interpretation and objective management:

* A rejected external raid becomes a reversal only when the first three
  completed confirmation minutes displace at least the detector's already
  frozen 12bp impulse magnitude in the opposite direction.
* A weaker reclaim is not traded immediately. The state remains pending for at
  most 120 completed minutes. If price closes back beyond the swept external
  boundary, the reclaim has failed and the original direction is traded as a
  reacceptance continuation. Otherwise the event expires as NO_TRADE.
* Migration-reclaim reversals retain the V6 full event-extreme target so the
  single portfolio slot is released.
* Strong external-reclaim reversals, value-edge continuations, external
  acceptances, and failed-reclaim reacceptances use their first causal liquidity
  objective as a protection trigger while retaining the existing net-R target.

The module produces a causal schedule only. NautilusTrader remains the sole
order, fill, fee, funding, margin, position, and NAV engine.
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
)
from derive_nt_lvcfr_v5_signals import (
    RANGE_MIGRATION_RECLAIM_REVERSAL,
    derive_v5,
)

FAILED_RECLAIM_REACCEPTANCE_CONTINUATION = (
    "FAILED_RECLAIM_REACCEPTANCE_CONTINUATION"
)
EVENT_EXTREME_FULL_TARGET = "EVENT_EXTREME_FULL_EXIT"
EVENT_EXTREME_PROTECTION = "EVENT_EXTREME_PROTECTION_TRIGGER"
PRIOR_EXTERNAL_PROTECTION = "PRIOR_RANGE_EXTERNAL_PROTECTION_TRIGGER"
PRIOR_EQUILIBRIUM_PROTECTION = "PRIOR_RANGE_EQUILIBRIUM_PROTECTION_TRIGGER"


def find_external_reacceptance(
    futures: dict[int, dict[str, float]],
    *,
    start_minute: int,
    original_direction: int,
    directional_external: float,
    expiry_minutes: int,
) -> tuple[int, list[dict[str, float]]] | None:
    """Return the first completed close reaccepted beyond swept liquidity."""
    if original_direction not in {-1, 1}:
        raise ValueError("original_direction must be -1 or 1")
    if expiry_minutes <= 0:
        raise ValueError("expiry_minutes must be positive")
    observed: list[dict[str, float]] = []
    for offset in range(expiry_minutes):
        minute = start_minute + offset
        row = futures.get(minute)
        if row is None:
            return None
        observed.append(row)
        if original_direction * (row["close"] - directional_external) > 0.0:
            return minute, observed
    return None


def derive_v7(
    *,
    source_signals: Path,
    raw_root: Path,
    output_signals: Path,
    output_manifest: Path,
    minimum_reclaim_displacement_bp: float = 12.0,
    failed_reclaim_expiry_minutes: int = 120,
    failed_reclaim_stop_buffer_atr: float = 0.20,
    **v5_kwargs: Any,
) -> list[dict[str, Any]]:
    if minimum_reclaim_displacement_bp <= 0.0:
        raise ValueError("minimum_reclaim_displacement_bp must be positive")
    if failed_reclaim_expiry_minutes <= 0:
        raise ValueError("failed_reclaim_expiry_minutes must be positive")
    if failed_reclaim_stop_buffer_atr < 0.0:
        raise ValueError("failed_reclaim_stop_buffer_atr must be non-negative")

    intermediate_manifest = output_manifest.with_name(
        output_manifest.stem + "-v5-intermediate.json"
    )
    signals = derive_v5(
        source_signals=source_signals,
        raw_root=raw_root,
        output_signals=output_signals,
        output_manifest=intermediate_manifest,
        **v5_kwargs,
    )
    futures = load_futures_minutes(raw_root)
    routed: list[dict[str, Any]] = []
    state_counts: dict[str, int] = {}
    objective_counts: dict[str, int] = {}
    weak_reclaims = 0
    weak_reclaims_reaccepted = 0
    weak_reclaims_expired = 0

    def count(mapping: dict[str, int], key: str) -> None:
        mapping[key] = mapping.get(key, 0) + 1

    for source in signals:
        signal = dict(source)
        details = dict(signal.get("details", {}))
        state = str(signal.get("scenario_kind", ""))
        signal.pop("structural_target", None)
        signal.pop("structural_protection_trigger", None)

        if state == RANGE_MIGRATION_RECLAIM_REVERSAL:
            direction = int(signal["direction"])
            target = float(
                details["event_high"] if direction > 0 else details["event_low"]
            )
            if not math.isfinite(target) or target <= 0.0:
                raise ValueError(f"invalid migration target {target}")
            signal["structural_target"] = target
            signal["target_mode"] = "STRUCTURAL_LIQUIDITY_OBJECTIVE"
            details["structural_objective"] = EVENT_EXTREME_FULL_TARGET
            count(objective_counts, EVENT_EXTREME_FULL_TARGET)

        elif state == VALUE_EDGE_CONTINUATION:
            trigger = float(details["directional_external"])
            signal["structural_protection_trigger"] = trigger
            signal["target_mode"] = "STRUCTURAL_PROTECTION_THEN_EXISTING_OBJECTIVE"
            details["structural_objective"] = PRIOR_EXTERNAL_PROTECTION
            count(objective_counts, PRIOR_EXTERNAL_PROTECTION)

        elif state == EXTERNAL_ACCEPTANCE_CONTINUATION:
            direction = int(signal["direction"])
            trigger = float(
                details["event_high"] if direction > 0 else details["event_low"]
            )
            signal["structural_protection_trigger"] = trigger
            signal["target_mode"] = "STRUCTURAL_PROTECTION_THEN_EXISTING_OBJECTIVE"
            details["structural_objective"] = EVENT_EXTREME_PROTECTION
            count(objective_counts, EVENT_EXTREME_PROTECTION)

        elif state == EXTERNAL_RECLAIM_REVERSAL:
            opposite_displacement = -float(details["directional_price_change_bp"])
            details["opposite_reclaim_displacement_bp"] = opposite_displacement
            details["minimum_reclaim_displacement_bp"] = (
                minimum_reclaim_displacement_bp
            )
            if opposite_displacement >= minimum_reclaim_displacement_bp:
                trigger = (
                    float(details["dealing_range_low"])
                    + float(details["dealing_range_high"])
                ) / 2.0
                signal["structural_protection_trigger"] = trigger
                signal["target_mode"] = (
                    "STRUCTURAL_PROTECTION_THEN_EXISTING_OBJECTIVE"
                )
                details["structural_objective"] = PRIOR_EQUILIBRIUM_PROTECTION
                count(objective_counts, PRIOR_EQUILIBRIUM_PROTECTION)
            else:
                weak_reclaims += 1
                original_direction = int(details["original_direction"])
                original_confirm_ns = int(details["v1_confirm_time_ns"])
                event_end_minute = original_confirm_ns // NS_PER_MINUTE
                evidence_minutes = int(details["external_evidence_minutes"])
                scan_start = event_end_minute + evidence_minutes
                directional_external = float(details["directional_external"])
                reacceptance = find_external_reacceptance(
                    futures,
                    start_minute=scan_start,
                    original_direction=original_direction,
                    directional_external=directional_external,
                    expiry_minutes=failed_reclaim_expiry_minutes,
                )
                if reacceptance is None:
                    weak_reclaims_expired += 1
                    continue

                reaccept_minute, observed_after_evidence = reacceptance
                event_start_minute = (
                    int(signal["first_start_time_ns"]) // NS_PER_MINUTE
                )
                observed: list[dict[str, float]] = []
                for minute in range(event_start_minute, reaccept_minute + 1):
                    row = futures.get(minute)
                    if row is None:
                        observed = []
                        break
                    observed.append(row)
                if not observed:
                    weak_reclaims_expired += 1
                    continue

                observed_low = min(row["low"] for row in observed)
                observed_high = max(row["high"] for row in observed)
                atr = float(signal["atr"])
                stop = (
                    observed_low - failed_reclaim_stop_buffer_atr * atr
                    if original_direction > 0
                    else observed_high + failed_reclaim_stop_buffer_atr * atr
                )
                trigger = float(
                    details["event_high"]
                    if original_direction > 0
                    else details["event_low"]
                )
                state = FAILED_RECLAIM_REACCEPTANCE_CONTINUATION
                signal["scenario_id"] = str(signal["scenario_id"]).replace(
                    "NT-LVCFR-V5-EXTERNAL_RECLAIM_REVERSAL-",
                    "NT-LVCFR-V7-FAILED_RECLAIM_REACCEPTANCE_CONTINUATION-",
                )
                signal["scenario_kind"] = state
                signal["entry_kind"] = "CONTINUATION"
                signal["direction"] = original_direction
                signal["confirm_time_ns"] = (
                    reaccept_minute + 1
                ) * NS_PER_MINUTE
                signal["eligible_time_ns"] = signal["confirm_time_ns"]
                signal["initial_stop"] = stop
                signal["structural_protection_trigger"] = trigger
                signal["target_mode"] = (
                    "STRUCTURAL_PROTECTION_THEN_EXISTING_OBJECTIVE"
                )
                signal["disable_rapid_failure_reversal"] = True
                details.update(
                    {
                        "scenario_kind": state,
                        "entry_kind": "CONTINUATION",
                        "routed_direction": original_direction,
                        "weak_reclaim_confirm_ns": (
                            original_confirm_ns + evidence_minutes * NS_PER_MINUTE
                        ),
                        "failed_reclaim_reaccept_minute": reaccept_minute,
                        "failed_reclaim_reaccept_close": (
                            observed_after_evidence[-1]["close"]
                        ),
                        "failed_reclaim_wait_minutes": (
                            reaccept_minute - scan_start + 1
                        ),
                        "failed_reclaim_expiry_minutes": (
                            failed_reclaim_expiry_minutes
                        ),
                        "observed_low_through_reacceptance": observed_low,
                        "observed_high_through_reacceptance": observed_high,
                        "structural_objective": EVENT_EXTREME_PROTECTION,
                    }
                )
                weak_reclaims_reaccepted += 1
                count(objective_counts, EVENT_EXTREME_PROTECTION)

        else:
            signal["target_mode"] = "EXISTING_NET_R_OBJECTIVE"
            details["structural_objective"] = "GENERIC_EXISTING_OBJECTIVE"
            count(objective_counts, "GENERIC_EXISTING_OBJECTIVE")

        signal["details"] = details
        routed.append(signal)
        count(state_counts, state)

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(routed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    v5_manifest = json.loads(intermediate_manifest.read_text(encoding="utf-8"))
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v7-sequential-auction",
        "engine_status": "causal_state_and_protection_schedule_only_no_backtest",
        "source_signal_count": v5_manifest["source_signal_count"],
        "v5_derived_signal_count": len(signals),
        "derived_signal_count": len(routed),
        "state_counts": state_counts,
        "objective_counts": objective_counts,
        "minimum_reclaim_displacement_bp": minimum_reclaim_displacement_bp,
        "failed_reclaim_expiry_minutes": failed_reclaim_expiry_minutes,
        "failed_reclaim_stop_buffer_atr": failed_reclaim_stop_buffer_atr,
        "weak_reclaims": weak_reclaims,
        "weak_reclaims_reaccepted": weak_reclaims_reaccepted,
        "weak_reclaims_expired": weak_reclaims_expired,
        "state_sequence": [
            "STRONG_RECLAIM_REVERSAL",
            "WEAK_RECLAIM_PENDING",
            FAILED_RECLAIM_REACCEPTANCE_CONTINUATION,
            "NO_TRADE_ON_EXPIRY",
        ],
        "full_exit_objectives": {
            RANGE_MIGRATION_RECLAIM_REVERSAL: EVENT_EXTREME_FULL_TARGET,
        },
        "protection_objectives": {
            VALUE_EDGE_CONTINUATION: PRIOR_EXTERNAL_PROTECTION,
            EXTERNAL_ACCEPTANCE_CONTINUATION: EVENT_EXTREME_PROTECTION,
            EXTERNAL_RECLAIM_REVERSAL: PRIOR_EQUILIBRIUM_PROTECTION,
            FAILED_RECLAIM_REACCEPTANCE_CONTINUATION: EVENT_EXTREME_PROTECTION,
        },
        "source_signals": str(source_signals),
        "output_signals": str(output_signals),
        "v5_intermediate_manifest": str(intermediate_manifest),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return routed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--minimum-reclaim-displacement-bp", type=float, default=12.0)
    parser.add_argument("--failed-reclaim-expiry-minutes", type=int, default=120)
    parser.add_argument("--failed-reclaim-stop-buffer-atr", type=float, default=0.20)
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
    routed = derive_v7(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        minimum_reclaim_displacement_bp=args.minimum_reclaim_displacement_bp,
        failed_reclaim_expiry_minutes=args.failed_reclaim_expiry_minutes,
        failed_reclaim_stop_buffer_atr=args.failed_reclaim_stop_buffer_atr,
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
        data_manifest["candidate"] = "candidate-03-nt-lvcfr-v7-sequential-auction"
        data_manifest["signals"] = len(routed)
        data_manifest["signal_path"] = output.as_posix()
        data_manifest["scenario_transform"] = {
            "type": "sequential_reclaim_reacceptance_with_structural_protection",
            "state_sequence": manifest["state_sequence"],
            "minimum_reclaim_displacement_bp": (
                args.minimum_reclaim_displacement_bp
            ),
            "failed_reclaim_expiry_minutes": args.failed_reclaim_expiry_minutes,
            "full_exit_objectives": manifest["full_exit_objectives"],
            "protection_objectives": manifest["protection_objectives"],
        }
        data_manifest_path.write_text(
            json.dumps(data_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v7-sequential-auction",
                "signals": len(routed),
                "state_counts": manifest["state_counts"],
                "objective_counts": manifest["objective_counts"],
                "weak_reclaims": manifest["weak_reclaims"],
                "weak_reclaims_reaccepted": manifest[
                    "weak_reclaims_reaccepted"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
