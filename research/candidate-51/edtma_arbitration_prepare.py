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
    "source_score": {"selection": "source_score", "breadth": 1, "majority": False, "btc": False},
    "freshest": {"selection": "freshest", "breadth": 1, "majority": False, "btc": False},
    "moderate_volume": {"selection": "moderate_volume", "breadth": 1, "majority": False, "btc": False},
    "breadth2_score": {"selection": "source_score", "breadth": 2, "majority": True, "btc": False},
    "breadth2_fresh": {"selection": "freshest", "breadth": 2, "majority": True, "btc": False},
    "breadth2_moderate": {"selection": "moderate_volume", "breadth": 2, "majority": True, "btc": False},
    "btc_anchor_score": {"selection": "source_score", "breadth": 1, "majority": False, "btc": True},
    "btc_anchor_fresh": {"selection": "freshest", "breadth": 1, "majority": False, "btc": True},
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
    "edtma_repair_management": "source",
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
}

LEGACY = {"sma_offset_low", "sma_offset_high", "sma_stop_min_fraction", "sma_stop_max_fraction", "sma_stop_atr_buffer"}


def manifest() -> dict:
    experiments = [name for name in VARIANTS if name != "source_score"]
    return {
        "family": "public_edtma_cross_asset_arbitration",
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": list(VARIANTS),
        "control_groups": {"source_score": experiments},
        "interpretation": (
            "The public source condition and winner engine are fixed. Compare only single-slot "
            "arbitration, peer breadth and BTC-anchor context by common/removed/added causal episodes."
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
                "edtma_arbitration_mode": variant["selection"],
                "edtma_min_same_side_breadth": variant["breadth"],
                "edtma_require_side_majority": variant["majority"],
                "edtma_require_btc_anchor": variant["btc"],
            }
        )
        (args.output / f"{name}.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    (args.output / "variants.txt").write_text("\n".join(VARIANTS) + "\n")
    (args.output / "MANIFEST.json").write_text(json.dumps(manifest(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
