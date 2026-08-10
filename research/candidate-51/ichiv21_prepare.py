from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

PERIODS = {
    "winter_2025_02": {
        "data_start": "2025-01-26", "start": "2025-02-01", "end": "2025-02-14",
    },
    "summer_2025_08": {
        "data_start": "2025-07-26", "start": "2025-08-01", "end": "2025-08-14",
    },
    "winter_2026_01": {
        "data_start": "2026-01-09", "start": "2026-01-15", "end": "2026-01-28",
    },
    "summer_2026_06": {
        "data_start": "2026-06-09", "start": "2026-06-15", "end": "2026-06-28",
    },
}

VARIANTS = {
    "source_all8_condition": {
        "episode": "condition_reentry", "direction": "long_only", "alignment": "all8",
        "stop": "source", "management": "source",
    },
    "source_all8_rising": {
        "episode": "rising_edge", "direction": "long_only", "alignment": "all8",
        "stop": "source", "management": "source",
    },
    "source_all8_no_signal": {
        "episode": "condition_reentry", "direction": "long_only", "alignment": "all8",
        "stop": "source", "management": "no_signal",
    },
    "source_all8_roi_progress": {
        "episode": "condition_reentry", "direction": "long_only", "alignment": "all8",
        "stop": "source", "management": "roi_progress",
    },
    "structural_all8_source": {
        "episode": "condition_reentry", "direction": "long_only", "alignment": "all8",
        "stop": "signal_extreme_ema120_atr", "management": "source",
    },
    "fast4_source": {
        "episode": "condition_reentry", "direction": "long_only", "alignment": "fast4",
        "stop": "source", "management": "source",
    },
    "slow4_source": {
        "episode": "condition_reentry", "direction": "long_only", "alignment": "slow4",
        "stop": "source", "management": "source",
    },
    "reciprocal_short_source": {
        "episode": "condition_reentry", "direction": "reciprocal_short", "alignment": "all8",
        "stop": "source", "management": "source",
    },
    "dual_source": {
        "episode": "condition_reentry", "direction": "dual", "alignment": "all8",
        "stop": "source", "management": "source",
    },
    "dual_structural_progress": {
        "episode": "condition_reentry", "direction": "dual", "alignment": "all8",
        "stop": "signal_extreme_ema120_atr", "management": "lifecycle_progress",
    },
}

COMMON = {
    "feature_max_age_seconds": 65.0,
    "cooldown_minutes": 0,
    "max_hold_minutes": 720,
    "funding_flatten_minute": 60,
    "funding_blackout_before_minutes": -1,
    "funding_blackout_after_minutes": -1,
    "ichiv21_bucket_minutes": 5,
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


def manifest() -> dict:
    return {
        "family": "public_ichiv21_accidental_long_horizon_fan",
        "public_code": {
            "repository": "remiotore/ccxt-freqtrade",
            "commit": "44beaeb6a420cd8e9f2e4ea93e11d6cfa192ee03",
            "path": "strategies/ichiV2_1.py",
            "performance_claim_used_as_evidence": False,
        },
        "search_clue": {
            "gist": "vjaykrsna/3aa41ada83ea890721e27ccda02c1d64",
            "reported_strategy_name": "ichiV2",
            "identity_with_public_code_asserted": False,
            "reason_for_priority": (
                "The report's ROI-dominated winners and losing exit-signal tail closely match the "
                "public ichiV2_1 policy, but only executable code is tested."
            ),
        },
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": list(VARIANTS),
        "control_groups": {
            "source_all8_condition": [
                "source_all8_rising", "source_all8_no_signal",
                "source_all8_roi_progress", "structural_all8_source",
                "fast4_source", "slow4_source", "dual_source",
            ],
            "reciprocal_short_source": ["dual_source", "dual_structural_progress"],
            "dual_source": ["dual_structural_progress"],
        },
        "interpretation": (
            "No result is reduced to a gate. Inspect how accidental long-horizon EMA alignment, "
            "fan acceleration, contiguous-condition re-entry, ROI, EMA-cross loss exits, reciprocal "
            "shorts and stop geometry each change opportunity, winners, losses and account path."
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
            "ichiv21_episode_mode": variant["episode"],
            "ichiv21_direction_mode": variant["direction"],
            "ichiv21_alignment_mode": variant["alignment"],
            "ichiv21_stop_mode": variant["stop"],
            "ichiv21_management_mode": variant["management"],
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
