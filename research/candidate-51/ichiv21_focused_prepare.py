from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

# Focused diagnosis of the externally sourced high-frequency ROI family.  The
# exhaustive ten-variant workflow was low-information and expensive; this run
# asks only the high-value question implied by the public report: preserve the
# ROI winner engine and isolate the losing EMA-cross exit / stop geometry.
PERIODS = {
    "autumn_2024_10": {"data_start": "2024-09-25", "start": "2024-10-01", "end": "2024-10-07"},
    "winter_2025_01": {"data_start": "2024-12-26", "start": "2025-01-01", "end": "2025-01-07"},
    "spring_2025_05": {"data_start": "2025-04-25", "start": "2025-05-01", "end": "2025-05-07"},
    "winter_2026_02": {"data_start": "2026-01-26", "start": "2026-02-01", "end": "2026-02-07"},
}

VARIANTS = {
    "source_exit": {"stop": "source", "management": "source"},
    "roi_only": {"stop": "source", "management": "no_signal"},
    "roi_progress": {"stop": "source", "management": "roi_progress"},
    "structural_roi_only": {"stop": "signal_extreme_ema120_atr", "management": "no_signal"},
}

COMMON = {
    "feature_max_age_seconds": 65.0,
    "cooldown_minutes": 0,
    "max_hold_minutes": 720,
    "funding_flatten_minute": 60,
    "funding_blackout_before_minutes": -1,
    "funding_blackout_after_minutes": -1,
    "ichiv21_bucket_minutes": 5,
    "ichiv21_episode_mode": "condition_reentry",
    "ichiv21_direction_mode": "long_only",
    "ichiv21_alignment_mode": "all8",
    "ichiv21_fan_gain_min": 1.002,
    "ichiv21_fan_shift_count": 3,
    "ichiv21_stop_fraction": 0.05,
    "ichiv21_remote_target_fraction": 0.05,
    "ichiv21_stop_atr_period": 14,
    "ichiv21_stop_atr_buffer": 0.25,
    "ichiv21_roi_0": 0.05,
    "ichiv21_roi_10": 0.03,
    "ichiv21_roi_41": 0.01,
    "ichiv21_roi_114": 0.0,
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
    for name, variant in VARIANTS.items():
        config = copy.deepcopy(base)
        for key in LEGACY:
            config["strategy"].pop(key, None)
        config["strategy"].update(COMMON)
        config["strategy"].update(
            {
                "ichiv21_stop_mode": variant["stop"],
                "ichiv21_management_mode": variant["management"],
            }
        )
        (args.output / f"{name}.json").write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n"
        )
    (args.output / "variants.txt").write_text("\n".join(VARIANTS) + "\n")
    manifest = {
        "family": "focused_public_ichiv21_roi_vs_exit_signal",
        "data_status": "development_mechanism_diagnosis",
        "public_code": {
            "repository": "remiotore/ccxt-freqtrade",
            "commit": "44beaeb6a420cd8e9f2e4ea93e11d6cfa192ee03",
            "path": "strategies/ichiV2_1.py",
        },
        "search_clue": {
            "gist": "vjaykrsna/3aa41ada83ea890721e27ccda02c1d64",
            "reported_roi_exits": 822,
            "reported_roi_win_rate": 0.943,
            "reported_exit_signal_exits": 234,
            "reported_exit_signal_win_rate": 0.145,
            "identity_with_public_code_asserted": False,
        },
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": list(VARIANTS),
        "control_groups": {
            "source_exit": ["roi_only", "roi_progress", "structural_roi_only"],
            "roi_only": ["roi_progress", "structural_roi_only"],
        },
        "question": (
            "On the four-asset one-slot account, does the executable public ROI mechanism create the "
            "claimed dense winner set, and is the EMA-cross exit or source stop the removable loss engine?"
        ),
    }
    (args.output / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
