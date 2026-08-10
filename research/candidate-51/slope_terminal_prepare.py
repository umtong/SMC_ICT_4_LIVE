from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

# These intervals are development data after the v47 untouched evaluation was
# inspected.  They deliberately contain both strong trailing regimes and the
# repeated-loss regimes that falsified the complete frozen system.
PERIODS = {
    "dev_2024_01": {"data_start": "2023-12-24", "start": "2024-01-01", "end": "2024-01-14"},
    "dev_2024_08": {"data_start": "2024-07-24", "start": "2024-08-01", "end": "2024-08-14"},
    "dev_2025_01": {"data_start": "2024-12-24", "start": "2025-01-01", "end": "2025-01-14"},
    "dev_2025_04": {"data_start": "2025-03-24", "start": "2025-04-01", "end": "2025-04-14"},
    "dev_2025_07": {"data_start": "2025-06-23", "start": "2025-07-01", "end": "2025-07-14"},
    "dev_2025_10": {"data_start": "2025-09-23", "start": "2025-10-01", "end": "2025-10-14"},
    "dev_2026_02": {"data_start": "2026-01-24", "start": "2026-02-01", "end": "2026-02-14"},
    "dev_2026_05": {"data_start": "2026-04-23", "start": "2026-05-01", "end": "2026-05-14"},
}

VARIANTS = {
    "ma_control": {"exit": "ma_only", "repair": "source", "terminal": "none"},
    "ma_progress_nonterminal": {"exit": "ma_only", "repair": "progress_thesis", "terminal": "none"},
    "ma_progress_terminal": {"exit": "ma_only", "repair": "progress_thesis", "terminal": "repair_exit"},
    "ma_condition_terminal": {"exit": "ma_only", "repair": "condition_loss", "terminal": "repair_exit"},
    "ma_any_loss_terminal": {"exit": "ma_only", "repair": "source", "terminal": "any_loss"},
    "ma_progress_any_loss": {"exit": "ma_only", "repair": "progress_thesis", "terminal": "any_loss"},
    "no_signal_any_loss": {"exit": "no_signal", "repair": "source", "terminal": "any_loss"},
    "no_signal_progress_any_loss": {"exit": "no_signal", "repair": "progress_thesis", "terminal": "any_loss"},
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
    experiments = [name for name in VARIANTS if name != "ma_control"]
    return {
        "family": "slope_terminal_causal_episode_repair",
        "data_status": "development_after_v47_untouched_failure_anatomy",
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": list(VARIANTS),
        "control_groups": {"ma_control": experiments},
        "fixed_components": [
            "public Slope entry",
            "2x public trailing-activation MA-separation geometry",
            "3%-NAV structural risk sizing and actual-fill validity",
            "public trailing and ROI winner engines",
        ],
        "causal_hypothesis": (
            "A contiguous source condition may contain multiple profitable entries, so rising-edge-only "
            "is too destructive.  However, after a causal no-progress/condition failure or a realized "
            "loss, immediate re-entry into that same run creates correlated loss cascades.  Mark only "
            "that run terminal, route to the next eligible symbol, and reset automatically on a fresh run."
        ),
        "interpretation": (
            "Compare gross-profit preservation, hard-stop and MA-exit compression, unique causal episodes, "
            "terminal-block side effects, trade frequency, actual-fill validity and one-slot NAV.  Do not "
            "select from final PnL alone."
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
                "slope_exit_mode": variant["exit"],
                "slope_repair_management": variant["repair"],
                "slope_terminal_mode": variant["terminal"],
            }
        )
        (args.output / f"{name}.json").write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n"
        )
    (args.output / "variants.txt").write_text("\n".join(VARIANTS) + "\n")
    (args.output / "MANIFEST.json").write_text(
        json.dumps(manifest(), indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
