from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

PERIODS = {
    "winter_2025_02": {
        "data_start": "2025-01-24", "start": "2025-02-01", "end": "2025-02-14",
    },
    "summer_2025_08": {
        "data_start": "2025-07-24", "start": "2025-08-01", "end": "2025-08-14",
    },
    "winter_2026_01": {
        "data_start": "2025-12-24", "start": "2026-01-01", "end": "2026-01-14",
    },
    "summer_2026_06": {
        "data_start": "2026-05-24", "start": "2026-06-01", "end": "2026-06-14",
    },
}

VARIANTS = {
    "structural_control": {"separation": 0.0, "exit": "corrected_symmetric", "repair": "source"},
    "sep1_control": {"separation": 1.0, "exit": "corrected_symmetric", "repair": "source"},
    "sep2_control": {"separation": 2.0, "exit": "corrected_symmetric", "repair": "source"},
    "sep1_progress": {"separation": 1.0, "exit": "corrected_symmetric", "repair": "progress_thesis"},
    "sep2_condition_loss": {"separation": 2.0, "exit": "corrected_symmetric", "repair": "condition_loss"},
    "sep2_progress": {"separation": 2.0, "exit": "corrected_symmetric", "repair": "progress_thesis"},
    "sep2_no_signal_condition_loss": {"separation": 2.0, "exit": "no_signal", "repair": "condition_loss"},
    "sep2_no_signal_progress": {"separation": 2.0, "exit": "no_signal", "repair": "progress_thesis"},
}

COMMON = {
    "feature_max_age_seconds": 65.0,
    "cooldown_minutes": 0,
    "max_hold_minutes": 2880,
    "funding_flatten_minute": 60,
    "funding_blackout_before_minutes": -1,
    "funding_blackout_after_minutes": -1,
    "slope_bucket_minutes": 60,
    "slope_episode_mode": "condition_reentry",
    "slope_direction_mode": "dual",
    "slope_adx_period": 14,
    "slope_rsi_period": 10,
    "slope_fast_period": 16,
    "slope_slow_period": 57,
    "slope_market_period": 97,
    "slope_lookback": 10,
    "slope_long_close_shift": 6,
    "slope_short_close_shift": 9,
    "slope_long_adx_min": 39.0,
    "slope_short_adx_min": 20.0,
    "slope_rsi_midline": 55.0,
    "slope_source_leverage": 2.0,
    "slope_source_stoploss_profit_ratio": 0.289,
    "slope_remote_target_fraction": 0.1415,
    "slope_stop_mode": "signal_slow_atr",
    "slope_stop_atr_period": 14,
    "slope_stop_atr_buffer": 0.25,
    "slope_exit_range_period": 9,
    "slope_trailing_positive_profit_ratio": 0.01,
    "slope_trailing_offset_profit_ratio": 0.021,
    "slope_roi_0_profit_ratio": 0.283,
    "slope_roi_132_profit_ratio": 0.16,
    "slope_roi_548_profit_ratio": 0.071,
    "slope_roi_961_profit_ratio": 0.0,
    "slope_progress_checkpoint_1_minutes": 132,
    "slope_progress_checkpoint_2_minutes": 548,
    "slope_progress_checkpoint_3_minutes": 961,
    "slope_progress_activation_fraction_1": 0.25,
    "slope_progress_activation_fraction_2": 0.50,
    "slope_progress_activation_fraction_3": 1.00,
}

LEGACY = {
    "sma_offset_low", "sma_offset_high", "sma_stop_min_fraction",
    "sma_stop_max_fraction", "sma_stop_atr_buffer",
}


def manifest() -> dict:
    return {
        "family": "public_slope_is_dope_source_relative_geometry_and_loss_repair",
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": list(VARIANTS),
        "control_groups": {
            "structural_control": [
                "sep1_control", "sep2_control", "sep1_progress",
                "sep2_condition_loss", "sep2_progress",
                "sep2_no_signal_condition_loss", "sep2_no_signal_progress",
            ],
            "sep2_control": [
                "sep2_condition_loss", "sep2_progress",
                "sep2_no_signal_condition_loss", "sep2_no_signal_progress",
            ],
        },
        "interpretation": (
            "The high-gross-profit structural-stop system is the control. Separation filters are "
            "defined only as 1x or 2x the public trailing activation. Loss repairs use source-thesis "
            "failure and the public ROI schedule. Compare gross-profit preservation, loss-tail "
            "compression, causal episode changes and implementation validity, not final PnL alone."
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
                "slope_min_separation_activation_multiple": variant["separation"],
                "slope_exit_mode": variant["exit"],
                "slope_repair_management": variant["repair"],
            }
        )
        (args.output / f"{name}.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    (args.output / "variants.txt").write_text("\n".join(VARIANTS) + "\n")
    (args.output / "MANIFEST.json").write_text(json.dumps(manifest(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
