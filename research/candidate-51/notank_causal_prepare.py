from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

PERIODS = {
    "source_winter_2024_02": {
        "data_start": "2024-01-24", "start": "2024-02-01", "end": "2024-02-14",
    },
    "source_spring_2024_05": {
        "data_start": "2024-04-24", "start": "2024-05-01", "end": "2024-05-14",
    },
    "later_summer_2025_08": {
        "data_start": "2025-07-24", "start": "2025-08-01", "end": "2025-08-14",
    },
    "later_summer_2026_06": {
        "data_start": "2026-05-24", "start": "2026-06-01", "end": "2026-06-14",
    },
}

VARIANTS = {
    "confirmed_source_long_r2": {
        "entry": "confirmed_pivot", "direction": "long_only", "episode": "all_confirmed",
        "management": "r_target", "target_r": 2.0,
    },
    "confirmed_reclaim_long_r15": {
        "entry": "confirmed_reclaim", "direction": "long_only", "episode": "all_confirmed",
        "management": "r_target", "target_r": 1.5,
    },
    "confirmed_reclaim_long_r2": {
        "entry": "confirmed_reclaim", "direction": "long_only", "episode": "all_confirmed",
        "management": "r_target", "target_r": 2.0,
    },
    "confirmed_reclaim_long_r3": {
        "entry": "confirmed_reclaim", "direction": "long_only", "episode": "all_confirmed",
        "management": "r_target", "target_r": 3.0,
    },
    "confirmed_reclaim_long_trail": {
        "entry": "confirmed_reclaim", "direction": "long_only", "episode": "all_confirmed",
        "management": "r_trail", "target_r": 6.0,
    },
    "confirmed_reclaim_long_opposite": {
        "entry": "confirmed_reclaim", "direction": "long_only", "episode": "all_confirmed",
        "management": "opposite_confirmed", "target_r": 6.0,
    },
    "confirmed_reclaim_long_progress": {
        "entry": "confirmed_reclaim", "direction": "long_only", "episode": "all_confirmed",
        "management": "progress", "target_r": 2.0,
    },
    "confirmed_reclaim_dual_r2": {
        "entry": "confirmed_reclaim", "direction": "dual", "episode": "all_confirmed",
        "management": "r_target", "target_r": 2.0,
    },
    "confirmed_reclaim_dual_trail": {
        "entry": "confirmed_reclaim", "direction": "dual", "episode": "all_confirmed",
        "management": "r_trail", "target_r": 6.0,
    },
    "rolling_long_r2": {
        "entry": "rolling_reclaim", "direction": "long_only", "episode": "rising_edge",
        "management": "r_target", "target_r": 2.0,
    },
    "rolling_long_trail": {
        "entry": "rolling_reclaim", "direction": "long_only", "episode": "rising_edge",
        "management": "r_trail", "target_r": 6.0,
    },
    "rolling_dual_r2": {
        "entry": "rolling_reclaim", "direction": "dual", "episode": "rising_edge",
        "management": "r_target", "target_r": 2.0,
    },
    "rolling_dual_progress": {
        "entry": "rolling_reclaim", "direction": "dual", "episode": "rising_edge",
        "management": "progress", "target_r": 2.0,
    },
}

COMMON = {
    "feature_max_age_seconds": 65.0,
    "cooldown_minutes": 0,
    "max_hold_minutes": 720,
    "funding_flatten_minute": 60,
    "funding_blackout_before_minutes": -1,
    "funding_blackout_after_minutes": -1,
    "notank_bucket_minutes": 15,
    "notank_pivot_order": 5,
    "notank_rsi_period": 14,
    "notank_long_rsi_max": 30.0,
    "notank_short_rsi_min": 70.0,
    "notank_stop_atr_buffer": 0.25,
    "notank_max_confirmation_atr": 2.5,
    "notank_min_reclaim_fraction": 0.0,
    "notank_rolling_window": 11,
    "notank_min_wick_fraction": 0.25,
    "notank_trail_activation_r": 1.0,
    "notank_trail_distance_r": 0.5,
    "notank_progress_minutes": 180,
    "notank_progress_mfe_r": 0.5,
}

LEGACY = {
    "sma_offset_low", "sma_offset_high", "sma_stop_min_fraction",
    "sma_stop_max_fraction", "sma_stop_atr_buffer",
}


def manifest() -> dict:
    return {
        "family": "public_notankai_extrema_causal_reconstruction",
        "source": {
            "repository": "TheoBrigitte/freqtrade",
            "commit": "b9feaaa2f845aed5612b3c7726a0590ee233c846",
            "path": "strategies/notankai/NOTankAi_15.py",
            "source_claim": {
                "period": "2024-01-16 through 2024-07-20",
                "max_open_trades": 6,
                "many_pair_trades": 5738,
                "daily_average_trades": 31.02,
                "average_daily_profit_percent": 16.55,
                "profit_factor": 7.44,
                "win_rate_percent": 99.9,
                "eth_trades": 322,
                "btc_trades": 0,
                "sol_trades": 0,
            },
            "implementation_defect": (
                "argrelextrema(order=5) labels each pivot using five future 15-minute candles, "
                "then the source enters and exits at the retrospectively labelled bar."
            ),
            "performance_claim_used_as_evidence": False,
        },
        "periods": PERIODS,
        "intervals": list(PERIODS),
        "variants": list(VARIANTS),
        "variant_dimensions": VARIANTS,
        "control_groups": {
            "confirmed_source_long_r2": ["confirmed_reclaim_long_r2"],
            "confirmed_reclaim_long_r2": [
                "confirmed_reclaim_long_r15",
                "confirmed_reclaim_long_r3",
                "confirmed_reclaim_long_trail",
                "confirmed_reclaim_long_opposite",
                "confirmed_reclaim_long_progress",
                "confirmed_reclaim_dual_r2",
            ],
            "rolling_long_r2": [
                "rolling_long_trail", "rolling_dual_r2",
            ],
            "rolling_dual_r2": ["rolling_dual_progress"],
        },
        "interpretation": (
            "Do not judge the source by belief or the causal variants by one metric. "
            "Separate the impossible retrospective label from the reusable extrema-reversal "
            "idea, then inspect confirmation delay, missed pivots, winner path, loss geometry, "
            "opposite-pivot exits, rolling rejection episodes and one-slot arbitration."
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
                "notank_entry_mode": variant["entry"],
                "notank_direction_mode": variant["direction"],
                "notank_episode_mode": variant["episode"],
                "notank_management_mode": variant["management"],
                "notank_target_r": variant["target_r"],
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
        "--base", type=Path,
        default=Path("research/candidate-51/config.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_configs(args.base, args.output)


if __name__ == "__main__":
    main()
