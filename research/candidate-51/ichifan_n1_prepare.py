from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

# Every interval was previously inspected by Candidate 47 and is development
# data.  This run is a focused mechanism comparison, not holdout evidence.
PERIODS = {
    "dev_2024_01": {"start": "2024-01-01", "end": "2024-01-07"},
    "dev_2024_08": {"start": "2024-08-01", "end": "2024-08-07"},
    "dev_2025_01": {"start": "2025-01-01", "end": "2025-01-07"},
    "dev_2025_04": {"start": "2025-04-01", "end": "2025-04-07"},
    "dev_2025_07": {"start": "2025-07-01", "end": "2025-07-07"},
    "dev_2025_10": {"start": "2025-10-01", "end": "2025-10-07"},
    "dev_2026_02": {"start": "2026-02-01", "end": "2026-02-07"},
    "dev_2026_05": {"start": "2026-05-01", "end": "2026-05-07"},
}

VARIANTS = {
    "structural_source_control": "source_control",
    "tight_trail_source_cross": "tight_trail_source_cross",
    "tight_trail_underwater_thesis": "tight_trail_underwater_thesis",
    "tight_trail_no_signal": "tight_trail_no_signal",
}

# Underlying-price values of the public Slope policy's 2x-leverage management:
# 2.1% source activation / 2 and 1.0% source distance / 2.
SLOPE_TRAIL_ACTIVATION = 0.0105
SLOPE_TRAIL_DISTANCE = 0.005


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        type=Path,
        default=Path("research/candidate-47/ichifan_structural_config.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base = json.loads(args.base.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    for name, mode in VARIANTS.items():
        config = copy.deepcopy(base)
        config["strategy"].update(
            {
                "ichifan_n1_management_mode": mode,
                "ichifan_n1_tight_trail_activation_fraction": SLOPE_TRAIL_ACTIVATION,
                "ichifan_n1_tight_trail_distance_fraction": SLOPE_TRAIL_DISTANCE,
            }
        )
        (args.output / f"{name}.json").write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    (args.output / "variants.txt").write_text(
        "\n".join(VARIANTS) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "family": "candidate47_ichifan_entry_plus_slope_trailing_n_to_1",
        "data_status": "development_after_candidate47_and_slope_anatomy",
        "accounting_warning": (
            "Each interval and variant is a fresh independent Nautilus account; "
            "results are never stitched into a continuous NAV."
        ),
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": list(VARIANTS),
        "control_groups": {
            "structural_source_control": [
                "tight_trail_source_cross",
                "tight_trail_underwater_thesis",
                "tight_trail_no_signal",
            ],
            "tight_trail_source_cross": [
                "tight_trail_underwater_thesis",
                "tight_trail_no_signal",
            ],
            "tight_trail_underwater_thesis": ["tight_trail_no_signal"],
        },
        "pinned_entry_component": {
            "branch": "research/candidate-47",
            "commit": "e01b7532829507320d0baae601b410746d523868",
            "files": [
                "ichifan_strategy.py",
                "ichifan_structural_strategy.py",
                "router.py",
                "run.py",
                "kline_only_inputs.py",
            ],
            "role": (
                "causal five-minute rising-edge IchiFan entry, four-asset one-slot "
                "arbitration and signal/cloud/90m structural invalidation"
            ),
        },
        "reused_winner_component": {
            "source_family": "public Slope-is-Dope trailing engine",
            "activation_fraction_underlying": SLOPE_TRAIL_ACTIVATION,
            "distance_fraction_underlying": SLOPE_TRAIL_DISTANCE,
            "source_leverage": 2.0,
            "source_activation_profit_ratio": 0.021,
            "source_distance_profit_ratio": 0.01,
            "role": (
                "capture the repeatedly observed fast winner path without changing "
                "the entry signal or structural risk budget"
            ),
        },
        "fixed_validity_contract": {
            "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "global_entry_or_position_limit": 1,
            "risk_fraction_current_nav": 0.03,
            "actual_fill_frozen_bracket_revalidation": True,
            "engine": "NautilusTrader BacktestNode",
        },
        "question": (
            "Does transplanting the already strong Slope trailing winner engine into "
            "Candidate 47's broader causal rising-edge entry preserve opportunity and "
            "remove the long loss tail without the same-episode churn that defeated "
            "earlier condition-reentry repairs?"
        ),
    }
    (args.output / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
