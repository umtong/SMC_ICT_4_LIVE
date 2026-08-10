from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

PERIODS = {
    "spring_2025_03": {"data_start": "2025-02-01", "start": "2025-03-01", "end": "2025-03-14"},
    "autumn_2025_09": {"data_start": "2025-08-01", "start": "2025-09-01", "end": "2025-09-14"},
    "winter_2026_01": {"data_start": "2025-12-01", "start": "2026-01-01", "end": "2026-01-14"},
    "summer_2026_06": {"data_start": "2026-05-01", "start": "2026-06-01", "end": "2026-06-14"},
}

VARIANTS = {
    "b2_control": {
        "entry_breadth": 2, "entry_btc": False,
        "own_repair": "source", "context_repair": "source",
        "dynamic_breadth": 2, "dynamic_btc": False,
    },
    "b2_context_loss": {
        "entry_breadth": 2, "entry_btc": False,
        "own_repair": "source", "context_repair": "context_loss",
        "dynamic_breadth": 2, "dynamic_btc": False,
    },
    "b2_condition_context": {
        "entry_breadth": 2, "entry_btc": False,
        "own_repair": "condition_loss", "context_repair": "context_loss",
        "dynamic_breadth": 2, "dynamic_btc": False,
    },
    "b2_progress_context": {
        "entry_breadth": 2, "entry_btc": False,
        "own_repair": "progress_thesis", "context_repair": "context_loss",
        "dynamic_breadth": 2, "dynamic_btc": False,
    },
    "b2_btc_control": {
        "entry_breadth": 2, "entry_btc": True,
        "own_repair": "source", "context_repair": "source",
        "dynamic_breadth": 2, "dynamic_btc": True,
    },
    "b2_btc_context": {
        "entry_breadth": 2, "entry_btc": True,
        "own_repair": "source", "context_repair": "context_loss",
        "dynamic_breadth": 2, "dynamic_btc": True,
    },
    "b3_control": {
        "entry_breadth": 3, "entry_btc": False,
        "own_repair": "source", "context_repair": "source",
        "dynamic_breadth": 3, "dynamic_btc": False,
    },
    "b3_context_loss": {
        "entry_breadth": 3, "entry_btc": False,
        "own_repair": "source", "context_repair": "context_loss",
        "dynamic_breadth": 3, "dynamic_btc": False,
    },
}

COMMON = {
    "feature_max_age_seconds": 65.0,
    "cooldown_minutes": 0,
    "max_hold_minutes": 2880,
    "funding_flatten_minute": 60,
    "funding_blackout_before_minutes": -1,
    "funding_blackout_after_minutes": -1,
    "edtma_bucket_minutes": 60,
    "edtma_episode_mode": "condition_reentry",
    "edtma_adx_period": 14,
    "edtma_volume_period": 22,
    "edtma_long_adx_min": 35.0,
    "edtma_long_tema_period": 7,
    "edtma_long_dema_period": 45,
    "edtma_long_ema_period": 177,
    "edtma_short_adx_min": 26.0,
    "edtma_short_tema_period": 19,
    "edtma_short_dema_period": 53,
    "edtma_short_ema_period": 102,
    "edtma_source_leverage": 3.0,
    "edtma_source_stoploss_profit_ratio": 0.12,
    "edtma_remote_target_fraction": 0.10,
    "edtma_stop_mode": "source",
    "edtma_exit_mode": "no_signal",
    "edtma_trailing_positive_profit_ratio": 0.01,
    "edtma_trailing_offset_profit_ratio": 0.02,
    "edtma_roi_0_profit_ratio": 0.238,
    "edtma_roi_362_profit_ratio": 0.148,
    "edtma_roi_881_profit_ratio": 0.066,
    "edtma_roi_1039_profit_ratio": 0.0,
    "edtma_long_chandelier_period": 23,
    "edtma_long_chandelier_multiple": 1.0,
    "edtma_short_chandelier_period": 26,
    "edtma_short_chandelier_multiple": 6.0,
    "edtma_progress_checkpoint_1_minutes": 120,
    "edtma_progress_checkpoint_2_minutes": 240,
    "edtma_progress_activation_fraction_1": 0.50,
    "edtma_progress_activation_fraction_2": 1.00,
    "edtma_arbitration_mode": "source_score",
    "edtma_require_side_majority": True,
    "edtma_dynamic_require_side_majority": True,
}

LEGACY = {"sma_offset_low", "sma_offset_high", "sma_stop_min_fraction", "sma_stop_max_fraction", "sma_stop_atr_buffer"}


def manifest() -> dict:
    return {
        "family": "public_edtma_peer_breadth_entry_and_dynamic_thesis",
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": list(VARIANTS),
        "control_groups": {
            "b2_control": [
                "b2_context_loss", "b2_condition_context", "b2_progress_context",
                "b2_btc_control", "b2_btc_context", "b3_control", "b3_context_loss",
            ],
            "b2_btc_control": ["b2_btc_context"],
            "b3_control": ["b3_context_loss"],
        },
        "interpretation": (
            "V42 established peer breadth as a high-value entry state but retained two spring hard-stop "
            "losses. These experiments keep source score, risk and trailing/ROI fixed and ask whether "
            "the peer state remains a valid thesis during the trade. Compare gross-profit preservation, "
            "hard-stop compression, re-entry side effects, actual-fill validity and independent episodes."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=Path("research/candidate-51/config.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads(args.base.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    for name, variant in VARIANTS.items():
        config = copy.deepcopy(base)
        for key in LEGACY:
            config["strategy"].pop(key, None)
        config["strategy"].update(COMMON)
        config["strategy"].update(
            {
                "edtma_min_same_side_breadth": variant["entry_breadth"],
                "edtma_require_btc_anchor": variant["entry_btc"],
                "edtma_repair_management": variant["own_repair"],
                "edtma_breadth_management": variant["context_repair"],
                "edtma_dynamic_min_same_side_breadth": variant["dynamic_breadth"],
                "edtma_dynamic_require_btc_anchor": variant["dynamic_btc"],
            }
        )
        (args.output / f"{name}.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    (args.output / "variants.txt").write_text("\n".join(VARIANTS) + "\n")
    (args.output / "MANIFEST.json").write_text(json.dumps(manifest(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
