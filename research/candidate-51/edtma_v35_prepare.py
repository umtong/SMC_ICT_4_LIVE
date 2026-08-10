from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

PERIODS = {
    "spring_2025_03": {
        "data_start": "2025-02-01", "start": "2025-03-01", "end": "2025-03-14",
    },
    "autumn_2025_09": {
        "data_start": "2025-08-01", "start": "2025-09-01", "end": "2025-09-14",
    },
    "winter_2026_01": {
        "data_start": "2025-12-01", "start": "2026-01-01", "end": "2026-01-14",
    },
    "summer_2026_06": {
        "data_start": "2026-05-01", "start": "2026-06-01", "end": "2026-06-14",
    },
}

VARIANTS = {
    "source_condition": {
        "episode": "condition_reentry", "exit": "source_exact", "stop": "source",
    },
    "source_rising_edge": {
        "episode": "rising_edge", "exit": "source_exact", "stop": "source",
    },
    "condition_no_signal": {
        "episode": "condition_reentry", "exit": "no_signal", "stop": "source",
    },
    "condition_rolling_chandelier": {
        "episode": "condition_reentry", "exit": "rolling_chandelier", "stop": "source",
    },
    "condition_structural_source_exit": {
        "episode": "condition_reentry", "exit": "source_exact", "stop": "signal_extreme_atr",
    },
    "condition_structural_no_signal": {
        "episode": "condition_reentry", "exit": "no_signal", "stop": "signal_extreme_atr",
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
    "edtma_stop_atr_period": 14,
    "edtma_stop_atr_buffer": 0.25,
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
}

LEGACY = {
    "sma_offset_low", "sma_offset_high", "sma_stop_min_fraction",
    "sma_stop_max_fraction", "sma_stop_atr_buffer",
}


def manifest() -> dict:
    return {
        "family": "public_edtma_tema_dema_ema_adx",
        "source": {
            "repository": "syuraj/freq-test",
            "commit": "f065569c4881ed646e6629f2509f1afa092f7227",
            "path": "user_data/strategies_backup/EDTMA_long_short_prot_CE_1h_3Lev_3mt_March.py",
            "performance_claim_used_as_evidence": False,
        },
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": list(VARIANTS),
        "control_groups": {
            "source_condition": [
                "source_rising_edge", "condition_no_signal",
                "condition_rolling_chandelier", "condition_structural_source_exit",
                "condition_structural_no_signal",
            ],
        },
        "interpretation": (
            "Do not select by final PnL alone. Inspect entry opportunity, repeated-entry increment, "
            "winner engine, stop tail, source chandelier behavior, structural risk geometry, "
            "path development and implementation validity."
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
        config["strategy"].update({
            "edtma_episode_mode": variant["episode"],
            "edtma_exit_mode": variant["exit"],
            "edtma_stop_mode": variant["stop"],
        })
        (output / f"{name}.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    (output / "variants.txt").write_text("\n".join(VARIANTS) + "\n")
    (output / "MANIFEST.json").write_text(json.dumps(manifest(), indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=Path("research/candidate-51/config.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_configs(args.base, args.output)


if __name__ == "__main__":
    main()
