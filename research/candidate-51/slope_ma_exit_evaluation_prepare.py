from __future__ import annotations

import argparse
import json
from pathlib import Path

# Frozen after v46 mechanism identification. None of these evaluation windows
# appeared in v40, v43, v45 or v46. The system is run once per independent
# account and is not altered from these results.
PERIODS = {
    "eval_2024_01": {"data_start": "2023-12-24", "start": "2024-01-01", "end": "2024-01-14"},
    "eval_2024_08": {"data_start": "2024-07-24", "start": "2024-08-01", "end": "2024-08-14"},
    "eval_2024_12": {"data_start": "2024-11-23", "start": "2024-12-01", "end": "2024-12-14"},
    "eval_2025_01": {"data_start": "2024-12-24", "start": "2025-01-01", "end": "2025-01-14"},
    "eval_2025_04": {"data_start": "2025-03-24", "start": "2025-04-01", "end": "2025-04-14"},
    "eval_2025_07": {"data_start": "2025-06-23", "start": "2025-07-01", "end": "2025-07-14"},
    "eval_2025_10": {"data_start": "2025-09-23", "start": "2025-10-01", "end": "2025-10-14"},
    "eval_2026_02": {"data_start": "2026-01-24", "start": "2026-02-01", "end": "2026-02-14"},
    "eval_2026_05": {"data_start": "2026-04-23", "start": "2026-05-01", "end": "2026-05-14"},
}

FROZEN = {
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
    "slope_exit_mode": "ma_only",
    "slope_exit_range_period": 9,
    "slope_trailing_positive_profit_ratio": 0.01,
    "slope_trailing_offset_profit_ratio": 0.021,
    "slope_roi_0_profit_ratio": 0.283,
    "slope_roi_132_profit_ratio": 0.16,
    "slope_roi_548_profit_ratio": 0.071,
    "slope_roi_961_profit_ratio": 0.0,
    "slope_min_separation_activation_multiple": 2.0,
    "slope_repair_management": "source",
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=Path("research/candidate-51/config.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.base.read_text())
    for key in LEGACY:
        config["strategy"].pop(key, None)
    config["strategy"].update(FROZEN)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "frozen_ma_cross.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n"
    )
    manifest = {
        "family": "frozen_slope_sep2_ma_cross_only_new_evaluation",
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": ["frozen_ma_cross"],
        "frozen_from": {
            "development_workflow": "candidate-51-slope-range-exit-ablation-v46",
            "development_variant": "ma_cross_only",
            "selection_reason": (
                "Across eleven development regimes it was positive in nine, reduced the worst "
                "interval loss from -3.29% to -0.80%, retained 212 valid trades over 154 days, "
                "and preserved a logical MA-thesis invalidation while deleting the repeatedly "
                "harmful rolling-range exit."
            ),
        },
        "data_status": (
            "Untouched evaluation for the frozen modified system. No listed interval was used in "
            "v40, v43, v45 or v46; results from this run do not alter the frozen configuration."
        ),
    }
    (args.output / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
