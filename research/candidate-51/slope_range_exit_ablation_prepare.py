from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

# All windows below are development data after v43/v45 inspection.  The purpose
# is mechanism identification: every valid public source exit observed so far
# was a losing rolling-range exit, while trailing exits were almost uniformly
# winners.  No result from this run is called holdout evidence.
PERIODS = {
    "dev_2024_03": {"data_start": "2024-02-22", "start": "2024-03-01", "end": "2024-03-14"},
    "dev_2024_06": {"data_start": "2024-05-24", "start": "2024-06-01", "end": "2024-06-14"},
    "dev_2024_10": {"data_start": "2024-09-23", "start": "2024-10-01", "end": "2024-10-14"},
    "dev_2025_02": {"data_start": "2025-01-24", "start": "2025-02-01", "end": "2025-02-14"},
    "dev_2025_05": {"data_start": "2025-04-23", "start": "2025-05-01", "end": "2025-05-14"},
    "dev_2025_08": {"data_start": "2025-07-24", "start": "2025-08-01", "end": "2025-08-14"},
    "dev_2025_11": {"data_start": "2025-10-24", "start": "2025-11-01", "end": "2025-11-14"},
    "dev_2026_01": {"data_start": "2025-12-24", "start": "2026-01-01", "end": "2026-01-14"},
    "dev_2026_03": {"data_start": "2026-02-21", "start": "2026-03-01", "end": "2026-03-14"},
    "dev_2026_06": {"data_start": "2026-05-24", "start": "2026-06-01", "end": "2026-06-14"},
    "dev_2026_07": {"data_start": "2026-06-23", "start": "2026-07-01", "end": "2026-07-14"},
}

VARIANTS = {
    "range_control": "corrected_symmetric",
    "ma_cross_only": "ma_only",
    "no_source_exit": "no_signal",
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
    args.output.mkdir(parents=True, exist_ok=True)
    for name, exit_mode in VARIANTS.items():
        config = copy.deepcopy(base)
        for key in LEGACY:
            config["strategy"].pop(key, None)
        config["strategy"].update(COMMON)
        config["strategy"]["slope_exit_mode"] = exit_mode
        (args.output / f"{name}.json").write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n"
        )
    (args.output / "variants.txt").write_text("\n".join(VARIANTS) + "\n")
    manifest = {
        "family": "slope_sep2_rolling_range_exit_ablation",
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": list(VARIANTS),
        "control_groups": {
            "range_control": ["ma_cross_only", "no_source_exit"],
            "ma_cross_only": ["no_source_exit"],
        },
        "data_status": "development_after_trade-by-trade_loss_engine_inspection",
        "fixed_components": [
            "public Slope entry",
            "2x public trailing-activation MA separation geometry",
            "3%-NAV structural risk sizing",
            "public trailing and ROI winner engines",
        ],
        "question": (
            "Does deleting only the rolling-range exit preserve the winner engine and remove the "
            "observed loss engine, and is the remaining MA-cross invalidation useful versus no source exit?"
        ),
    }
    (args.output / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
