from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

# These windows were not used to derive or compare the Slope 2x-activation
# geometry rule in v40/v43.  The frozen system is evaluated once on each.
PERIODS = {
    "eval_2024_03": {"data_start": "2024-02-22", "start": "2024-03-01", "end": "2024-03-14"},
    "eval_2024_06": {"data_start": "2024-05-24", "start": "2024-06-01", "end": "2024-06-14"},
    "eval_2024_10": {"data_start": "2024-09-23", "start": "2024-10-01", "end": "2024-10-14"},
    "eval_2025_05": {"data_start": "2025-04-23", "start": "2025-05-01", "end": "2025-05-14"},
    "eval_2025_11": {"data_start": "2025-10-24", "start": "2025-11-01", "end": "2025-11-14"},
    "eval_2026_03": {"data_start": "2026-02-21", "start": "2026-03-01", "end": "2026-03-14"},
    "eval_2026_07": {"data_start": "2026-06-23", "start": "2026-07-01", "end": "2026-07-14"},
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
    "slope_exit_mode": "corrected_symmetric",
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
    base = json.loads(args.base.read_text())
    for key in LEGACY:
        base["strategy"].pop(key, None)
    base["strategy"].update(FROZEN)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "frozen_sep2.json").write_text(
        json.dumps(base, indent=2, sort_keys=True) + "\n"
    )
    manifest = {
        "family": "frozen_slope_sep2_new_evaluation",
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": ["frozen_sep2"],
        "frozen_from": {
            "development_workflow": "candidate-51-slope-repair-v43",
            "development_variant": "sep2_control",
            "selection_reason": (
                "The 2x public trailing-activation separation rule produced positive after-cost "
                "single-account results in all four development regimes while retaining 26-33 "
                "trades per 14 days; no parameter is changed for this evaluation."
            ),
        },
        "data_status": (
            "New evaluation for this frozen modified system. These windows were not used in v40/v43 "
            "to derive or select the separation rule. Results are not used to alter this run."
        ),
    }
    (args.output / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
