from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

PERIODS = {
    "dev_2024_01": {"start": "2024-01-01", "end": "2024-01-07"},
    "dev_2024_08": {"start": "2024-08-01", "end": "2024-08-07"},
    "dev_2025_01": {"start": "2025-01-01", "end": "2025-01-07"},
    "dev_2025_04": {"start": "2025-04-01", "end": "2025-04-07"},
    "dev_2025_07": {"start": "2025-07-01", "end": "2025-07-07"},
    "dev_2025_10": {"start": "2025-10-01", "end": "2025-10-07"},
    "dev_2026_02": {"start": "2026-02-01", "end": "2026-02-07"},
    "dev_2026_05": {"start": "2026-05-01", "end": "2026-05-07"},
}

VARIANTS = {
    "score0_source_control": 0.0,
    "score17_strong_fan": 17.0,
}


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
    args.output.mkdir(parents=True, exist_ok=True)
    for name, minimum in VARIANTS.items():
        config = copy.deepcopy(base)
        config["strategy"]["ichifan_min_entry_score"] = minimum
        (args.output / f"{name}.json").write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (args.output / "variants.txt").write_text(
        "\n".join(VARIANTS) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "family": "candidate47_ichifan_source_score_strong_state",
        "data_status": (
            "development after chronological anatomy of Candidate 47 source-control trades; "
            "score 17 requires a new frozen evaluation if this mechanism remains promising"
        ),
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": list(VARIANTS),
        "control_groups": {"score0_source_control": ["score17_strong_fan"]},
        "fixed_components": [
            "public causal IchiFan entry conditions",
            "four-asset source-score arbitration",
            "rising-edge causal episode semantics",
            "signal/cloud/90-minute structural stop",
            "90-minute trend-cross exit and 8%/6% source trailing",
            "NautilusTrader costs, fills and current-NAV 3% loss budget",
        ],
        "state_definition": {
            "source_score_formula": (
                "10000*max(fan_gain-1,0) + 100*max(fan_magnitude-1,0) + "
                "10*max(shifted_close/cloud_top-1,0)"
            ),
            "control_minimum": 0.0,
            "strong_fan_minimum": 17.0,
            "reason": (
                "use the mechanism's existing causal cross-sectional score as a complete "
                "pre-entry acceleration/runway state rather than adding an unrelated indicator"
            ),
        },
        "question": (
            "Does the public IchiFan mechanism's own source score separate a persistent "
            "strong-fan state that preserves the large winner engine while reducing hard-stop "
            "and weak trend-cross loss episodes across regimes?"
        ),
        "accounting_warning": "Independent development accounts are not stitched into continuous NAV.",
    }
    (args.output / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
