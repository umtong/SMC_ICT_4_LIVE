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
    "breadth1_source_control": 1,
    "breadth2_static_context": 2,
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
        config["strategy"]["ichifan_static_breadth_min"] = minimum
        (args.output / f"{name}.json").write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (args.output / "variants.txt").write_text(
        "\n".join(VARIANTS) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "family": "candidate47_ichifan_static_same_direction_breadth",
        "data_status": "development_after_candidate47_and_edtma_breadth_results",
        "intervals": list(PERIODS),
        "periods": PERIODS,
        "variants": list(VARIANTS),
        "control_groups": {
            "breadth1_source_control": ["breadth2_static_context"],
        },
        "fixed_components": [
            "Candidate 47 causal rising-edge IchiFan entry",
            "Candidate 47 source score arbitration",
            "Candidate 47 structural stop",
            "Candidate 47 90-minute cross and 8%/6% source trail",
            "NautilusTrader execution, costs, current-NAV 3% loss budget",
        ],
        "reused_component": {
            "source": "Candidate 51 EDTMA static breadth-2 entry context",
            "role": (
                "require at least two simultaneously active same-direction source "
                "conditions at entry, without using breadth as a dynamic exit"
            ),
            "threshold": 2,
        },
        "question": (
            "Are Candidate 47's loss episodes concentrated in isolated single-asset "
            "fan states, while its large source-managed winners survive a fixed "
            "two-of-four same-direction context?"
        ),
        "accounting_warning": "Independent diagnostic accounts are not stitched into continuous NAV.",
    }
    (args.output / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
