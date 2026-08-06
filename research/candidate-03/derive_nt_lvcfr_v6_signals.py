#!/usr/bin/env python3
"""Attach causal liquidity objectives to V5 reversal states.

V6 changes no detector, entry, invalidation, risk budget, fee, slippage, funding,
or position-sizing rule. It preserves the V5 mutually exclusive state router and
replaces only the generic fixed-R objective for two reversal states:

- RANGE_MIGRATION_RECLAIM_REVERSAL targets the ten-minute event extreme in the
  migration direction. A reclaimed counter-trend liquidation has completed its
  first causal objective when that impulse origin is fully traversed.
- EXTERNAL_RECLAIM_REVERSAL targets the equilibrium (midpoint) of the preceding
  240-minute dealing range. After an external raid is rejected, equilibrium is
  the first internally defined draw on liquidity.

The output is still only a causal signal schedule. NautilusTrader remains the
sole execution, fill, fee, funding, margin, position, and NAV engine.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from derive_nt_lvcfr_v4_signals import EXTERNAL_RECLAIM_REVERSAL
from derive_nt_lvcfr_v5_signals import (
    RANGE_MIGRATION_RECLAIM_REVERSAL,
    derive_v5,
)

EVENT_EXTREME_OBJECTIVE = "EVENT_EXTREME_IN_RECLAIM_DIRECTION"
DEALING_RANGE_EQUILIBRIUM_OBJECTIVE = "PRIOR_240M_DEALING_RANGE_EQUILIBRIUM"


def attach_structural_targets(signals: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        EVENT_EXTREME_OBJECTIVE: 0,
        DEALING_RANGE_EQUILIBRIUM_OBJECTIVE: 0,
        "GENERIC_EXISTING_OBJECTIVE": 0,
    }
    for signal in signals:
        state = str(signal.get("scenario_kind", ""))
        direction = int(signal["direction"])
        details = dict(signal.get("details", {}))
        target: float | None = None
        objective = "GENERIC_EXISTING_OBJECTIVE"

        if state == RANGE_MIGRATION_RECLAIM_REVERSAL:
            target = float(details["event_high"] if direction > 0 else details["event_low"])
            objective = EVENT_EXTREME_OBJECTIVE
        elif state == EXTERNAL_RECLAIM_REVERSAL:
            target = (
                float(details["dealing_range_low"])
                + float(details["dealing_range_high"])
            ) / 2.0
            objective = DEALING_RANGE_EQUILIBRIUM_OBJECTIVE

        if target is not None:
            if not math.isfinite(target) or target <= 0.0:
                raise ValueError(f"invalid structural target {target} for {signal['scenario_id']}")
            signal["structural_target"] = target
            signal["target_mode"] = "STRUCTURAL_LIQUIDITY_OBJECTIVE"
            details["structural_target"] = target
            details["structural_objective"] = objective
        else:
            signal.pop("structural_target", None)
            signal["target_mode"] = "EXISTING_NET_R_OBJECTIVE"
            details["structural_objective"] = objective

        signal["details"] = details
        counts[objective] += 1
    return counts


def derive_v6(
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
    intermediate_manifest = output_manifest.with_name(
        output_manifest.stem + "-v5-intermediate.json"
    )
    signals = derive_v5(
        source_signals=source_signals,
        raw_root=raw_root,
        output_signals=output_signals,
        output_manifest=intermediate_manifest,
        dealing_range_minutes=dealing_range_minutes,
        minimum_origin_alignment=minimum_origin_alignment,
        minimum_acceptance_fraction=minimum_acceptance_fraction,
        minimum_opposite_migration_fraction=minimum_opposite_migration_fraction,
        migration_reclaim_expiry_minutes=migration_reclaim_expiry_minutes,
        external_evidence_minutes=external_evidence_minutes,
        minimum_directional_spot_flow=minimum_directional_spot_flow,
        detector_minimum_total_oi_drop_bp=detector_minimum_total_oi_drop_bp,
        maximum_external_acceptance_oi_multiple=maximum_external_acceptance_oi_multiple,
        boundary_stop_buffer_atr=boundary_stop_buffer_atr,
        reversal_stop_buffer_atr=reversal_stop_buffer_atr,
    )
    target_counts = attach_structural_targets(signals)
    output_signals.write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    v5_manifest = json.loads(intermediate_manifest.read_text(encoding="utf-8"))
    manifest = {
        **v5_manifest,
        "candidate": "candidate-03-nt-lvcfr-v6-structural-objectives",
        "engine_status": "causal_state_and_objective_schedule_only_no_backtest",
        "target_counts": target_counts,
        "structural_objectives": {
            RANGE_MIGRATION_RECLAIM_REVERSAL: EVENT_EXTREME_OBJECTIVE,
            EXTERNAL_RECLAIM_REVERSAL: DEALING_RANGE_EQUILIBRIUM_OBJECTIVE,
        },
        "unchanged_existing_objectives": [
            "VALUE_EDGE_CONTINUATION",
            "EXTERNAL_ACCEPTANCE_CONTINUATION",
        ],
        "v5_intermediate_manifest": str(intermediate_manifest),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return signals


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
    signals = derive_v6(
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
        data_manifest["candidate"] = "candidate-03-nt-lvcfr-v6-structural-objectives"
        data_manifest["signals"] = len(signals)
        data_manifest["signal_path"] = output.as_posix()
        data_manifest["scenario_transform"] = {
            "type": "v5_state_router_with_state_specific_structural_objectives",
            "state_priority": manifest["state_priority"],
            "structural_objectives": manifest["structural_objectives"],
            "dealing_range_minutes": args.dealing_range_minutes,
            "migration_reclaim_expiry_minutes": args.migration_reclaim_expiry_minutes,
        }
        data_manifest_path.write_text(
            json.dumps(data_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v6-structural-objectives",
                "signals": len(signals),
                "state_counts": manifest["state_counts"],
                "target_counts": manifest["target_counts"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
