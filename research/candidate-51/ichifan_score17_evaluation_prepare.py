from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

# None of these scored windows was used by the v52 combined-system development
# comparison.  Several underlying dates may have appeared in older unrelated
# family research, so this is described precisely as new evaluation for the
# frozen score-17 combined system, not as universally untouched market data.
PERIODS = {
    "eval_2024_02": {"start": "2024-02-15", "end": "2024-02-21"},
    "eval_2024_05": {"start": "2024-05-15", "end": "2024-05-21"},
    "eval_2024_11": {"start": "2024-11-15", "end": "2024-11-21"},
    "eval_2025_02": {"start": "2025-02-15", "end": "2025-02-21"},
    "eval_2025_06": {"start": "2025-06-15", "end": "2025-06-21"},
    "eval_2025_12": {"start": "2025-12-15", "end": "2025-12-21"},
    "eval_2026_01": {"start": "2026-01-15", "end": "2026-01-21"},
    "eval_2026_06": {"start": "2026-06-15", "end": "2026-06-21"},
}

VARIANT = "frozen_score17"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        type=Path,
        default=Path("research/candidate-47/ichifan_structural_config.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads(args.base.read_text(encoding="utf-8"))
    config = copy.deepcopy(base)
    config["strategy"]["ichifan_min_entry_score"] = 17.0
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / f"{VARIANT}.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output / "variants.txt").write_text(VARIANT + "\n", encoding="utf-8")
    manifest = {
        "family": "frozen_candidate47_ichifan_score17_new_evaluation",
        "data_status": (
            "new evaluation for the frozen score-17 combined system; no result from "
            "these scored windows is used to change the system in this run"
        ),
        "frozen_from": {
            "development_workflow": "candidate-51-ichifan-strong-score-v52",
            "development_variant": "score17_strong_fan",
            "minimum_source_score": 17.0,
            "development_accounts": 8,
            "development_positive_accounts": 4,
            "development_valid_trades": 55,
            "development_trades_per_day": 0.982,
            "development_profit_factor": 1.437,
            "development_net_pnl_usdt": 16387.63,
            "development_expectancy_r": 0.103,
            "development_median_geometric_daily_growth": 0.0007,
            "reason_for_evaluation": (
                "The fixed source-relative state preserved nearly one independent trade per day, "
                "turned aggregate profit factor above one and reduced loss burden materially, but "
                "had only four positive development accounts; new intervals are the cheapest way "
                "to distinguish a reusable state from a development coincidence."
            ),
        },
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": [VARIANT],
        "control_groups": {},
        "fixed_components": [
            "Candidate 47 public causal IchiFan entry",
            "Candidate 47 source-score arbitration",
            "minimum source score exactly 17",
            "rising-edge causal episode semantics",
            "signal/cloud/90-minute structural stop",
            "90-minute cross exit and 8%/6% source trailing",
            "BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT one global slot",
            "NautilusTrader costs, fills and current-NAV 3% loss budget",
        ],
        "accounting_warning": (
            "Evaluation intervals are independent accounts for regime diagnosis and are not "
            "stitched into a continuous NAV."
        ),
        "interpretation": (
            "Inspect opportunity, gross-profit preservation, loss burden, account dispersion and "
            "causal episode count together.  A positive aggregate produced by one interval is not "
            "enough; no threshold is changed after these results."
        ),
    }
    (args.output / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
