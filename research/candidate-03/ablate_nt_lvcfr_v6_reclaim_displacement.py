#!/usr/bin/env python3
"""Remove only weak external-reclaim observations from a V6 schedule.

The ablated core variable is the magnitude of opposite displacement after an
external raid. A reversal survives only when its three completed confirmation
minutes displace at least as far as the frozen detector's original 12bp
impulse definition. No entry, stop, target, risk, fee, funding, or execution
parameter is changed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from derive_nt_lvcfr_v4_signals import EXTERNAL_RECLAIM_REVERSAL


def ablate(
    signals: list[dict],
    *,
    minimum_opposite_displacement_bp: float,
) -> tuple[list[dict], dict]:
    kept: list[dict] = []
    removed: list[dict] = []
    for signal in signals:
        state = str(signal.get("scenario_kind", ""))
        opposite_displacement = -float(
            signal.get("details", {}).get("directional_price_change_bp", 0.0)
        )
        if (
            state == EXTERNAL_RECLAIM_REVERSAL
            and opposite_displacement < minimum_opposite_displacement_bp
        ):
            removed.append(
                {
                    "scenario_id": signal["scenario_id"],
                    "scenario_kind": state,
                    "opposite_displacement_bp": opposite_displacement,
                }
            )
            continue
        kept.append(signal)
    manifest = {
        "ablation": "REMOVE_WEAK_EXTERNAL_RECLAIM_DISPLACEMENT",
        "minimum_opposite_displacement_bp": minimum_opposite_displacement_bp,
        "source_signal_count": len(signals),
        "kept_signal_count": len(kept),
        "removed_signal_count": len(removed),
        "removed": removed,
    }
    return kept, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--minimum-opposite-displacement-bp", type=float, default=12.0)
    args = parser.parse_args()

    signals_path = args.signals.resolve()
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    kept, manifest = ablate(
        signals,
        minimum_opposite_displacement_bp=args.minimum_opposite_displacement_bp,
    )
    signals_path.write_text(
        json.dumps(kept, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.output_manifest.resolve().write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
