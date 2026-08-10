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
    "condition_source_source": {
        "reentry": "condition", "stop_policy": "source_all",
        "nonquality": "source", "management": "source",
    },
    "profit_source_source": {
        "reentry": "profit_capture", "stop_policy": "source_all",
        "nonquality": "source", "management": "source",
    },
    "condition_source_progress": {
        "reentry": "condition", "stop_policy": "source_all",
        "nonquality": "source", "management": "progress_only",
    },
    "profit_source_progress": {
        "reentry": "profit_capture", "stop_policy": "source_all",
        "nonquality": "source", "management": "progress_only",
    },
    "condition_structural_source": {
        "reentry": "condition", "stop_policy": "structural_all",
        "nonquality": "source", "management": "source",
    },
    "profit_structural_source": {
        "reentry": "profit_capture", "stop_policy": "structural_all",
        "nonquality": "source", "management": "source",
    },
    "condition_structural_progress": {
        "reentry": "condition", "stop_policy": "structural_all",
        "nonquality": "source", "management": "progress_only",
    },
    "profit_structural_progress": {
        "reentry": "profit_capture", "stop_policy": "structural_all",
        "nonquality": "source", "management": "progress_only",
    },
    "condition_hybrid_source": {
        "reentry": "condition", "stop_policy": "source_fresh_structural_profit",
        "nonquality": "source", "management": "source",
    },
    "profit_hybrid_source": {
        "reentry": "profit_capture", "stop_policy": "source_fresh_structural_profit",
        "nonquality": "source", "management": "source",
    },
    "condition_hybrid_progress": {
        "reentry": "condition", "stop_policy": "source_fresh_structural_profit",
        "nonquality": "source", "management": "progress_only",
    },
    "profit_hybrid_progress": {
        "reentry": "profit_capture", "stop_policy": "source_fresh_structural_profit",
        "nonquality": "source", "management": "progress_only",
    },
    "profit_quality_source_source": {
        "reentry": "profit_capture", "stop_policy": "quality_hybrid",
        "nonquality": "source", "management": "source",
    },
    "profit_quality_source_progress": {
        "reentry": "profit_capture", "stop_policy": "quality_hybrid",
        "nonquality": "source", "management": "progress_only",
    },
    "profit_quality_skip_source": {
        "reentry": "profit_capture", "stop_policy": "quality_hybrid",
        "nonquality": "skip", "management": "source",
    },
    "profit_quality_skip_progress": {
        "reentry": "profit_capture", "stop_policy": "quality_hybrid",
        "nonquality": "skip", "management": "progress_only",
    },
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
    "slope_stop_mode": "source",
    "slope_stop_atr_period": 14,
    "slope_stop_atr_buffer": 0.25,
    "slope_exit_mode": "no_signal",
    "slope_exit_range_period": 9,
    "slope_trailing_positive_profit_ratio": 0.01,
    "slope_trailing_offset_profit_ratio": 0.021,
    "slope_roi_0_profit_ratio": 0.283,
    "slope_roi_132_profit_ratio": 0.16,
    "slope_roi_548_profit_ratio": 0.071,
    "slope_roi_961_profit_ratio": 0.0,
    "slope_hybrid_progress_checkpoint_1_minutes": 360,
    "slope_hybrid_progress_checkpoint_2_minutes": 960,
    "slope_hybrid_progress_activation_fraction_1": 0.25,
    "slope_hybrid_progress_activation_fraction_2": 1.0,
    "slope_hybrid_adx_margin_min": 5.0,
    "slope_hybrid_ma_separation_min": 0.005,
    "slope_hybrid_ma_separation_max": 0.070,
    "slope_hybrid_slope_strength_min": 0.00075,
    "slope_hybrid_slope_strength_max": 0.0080,
    "slope_hybrid_long_rsi_max": 90.0,
    "slope_hybrid_short_rsi_min": 20.0,
}

LEGACY = {
    "sma_offset_low", "sma_offset_high", "sma_stop_min_fraction",
    "sma_stop_max_fraction", "sma_stop_atr_buffer",
}


def manifest() -> dict:
    return {
        "family": "public_slope_is_dope_episode_risk_lifecycle_factorial",
        "source": {
            "repository": "syuraj/freq-test",
            "commit": "f065569c4881ed646e6629f2509f1afa092f7227",
            "path": "user_data/strategies/picasso_slope_is_dope_adx_1h_2Lev_dec15_3mt.py",
            "source_claim": {
                "period": "2022-01-01 through 2022-12-08",
                "trades": 3814,
                "daily_average_trades": 11.18,
                "average_daily_profit_percent": 1.42,
                "profit_factor": 1.53,
                "trailing_exit_profit_percent": 2595.06,
                "exit_signal_profit_percent": -1824.08,
            },
            "performance_claim_used_as_evidence": False,
        },
        "periods": PERIODS,
        "intervals": list(PERIODS),
        "variants": list(VARIANTS),
        "variant_dimensions": VARIANTS,
        "control_groups": {
            "condition_source_source": [
                "profit_source_source",
                "condition_source_progress",
                "condition_structural_source",
                "condition_hybrid_source",
            ],
            "profit_source_source": [
                "profit_source_progress",
                "profit_structural_source",
                "profit_hybrid_source",
                "profit_quality_source_source",
                "profit_quality_skip_source",
            ],
            "profit_structural_source": ["profit_structural_progress"],
            "profit_hybrid_source": ["profit_hybrid_progress"],
            "profit_quality_source_source": [
                "profit_quality_source_progress",
                "profit_quality_skip_source",
            ],
            "profit_quality_skip_source": ["profit_quality_skip_progress"],
        },
        "interpretation": (
            "This is a clean mechanism factorial, not a gate. Compare the same "
            "causal signal across episode policy, actual risk geometry, quality "
            "routing and lifecycle. Preserve winner mass, identify tail-loss "
            "concentration, inspect rejected actionable no-trades, and separate "
            "implementation validity from market logic."
        ),
    }


def write_configs(base_path: Path, output: Path) -> None:
    base = json.loads(base_path.read_text())
    output.mkdir(parents=True, exist_ok=True)
    for name, variant in VARIANTS.items():
        config = copy.deepcopy(base)
        for key in LEGACY:
            config["strategy"].pop(key, None)
        config["strategy"].update(COMMON)
        config["strategy"].update(
            {
                "slope_hybrid_reentry_policy": variant["reentry"],
                "slope_hybrid_stop_policy": variant["stop_policy"],
                "slope_hybrid_nonquality_action": variant["nonquality"],
                "slope_hybrid_management": variant["management"],
            }
        )
        (output / f"{name}.json").write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n"
        )
    (output / "variants.txt").write_text("\n".join(VARIANTS) + "\n")
    (output / "MANIFEST.json").write_text(
        json.dumps(manifest(), indent=2, sort_keys=True) + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        type=Path,
        default=Path("research/candidate-51/config.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_configs(args.base, args.output)


if __name__ == "__main__":
    main()
