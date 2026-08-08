#!/usr/bin/env python3
"""Run v56 funded runner with/without pre-event reversal ownership."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "funded-runner-transfer-state",
    "funded-runner-reversal-ownership",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v56 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1"
    os.environ["C10_V40_SOURCE_EQUILIBRIUM_DETECTOR"] = "1"
    os.environ["C10_V36_CE_REJECTION"] = "0"
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1"
    os.environ["C10_V41_SOURCE_ENTRY_MODE"] = "FIRST_DISPLACEMENT_NEAR_EDGE"
    os.environ["C10_V44_PRIMARY_TARGET_MODE"] = "SOURCE_EQUILIBRIUM"
    os.environ["C10_V45_INVALIDATION_MODE"] = "SOURCE_RAID_EXTREME"
    os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_REFERENCE"] = "EXPECTED_ENTRY"
    os.environ["C10_V46_VOID_CLOSE_EXIT"] = "0"
    os.environ["C10_V47_EVENT_LEADER_ONLY"] = "1"
    os.environ["C10_V49_TRANSFER_STATE_ROUTER"] = "1"
    os.environ["C10_V50_INTERNAL_DEALING_RANGE"] = "0"
    os.environ["C10_V51_SIZE_DEPENDENT_REWARD"] = "1"
    os.environ["C10_V52_EXTERNAL_RUNNER"] = "1"
    os.environ["C10_V52_FUNDED_EQUILIBRIUM"] = "1"
    os.environ["C10_V56_REVERSAL_OWNERSHIP"] = (
        "1" if variant.endswith("reversal-ownership") else "0"
    )


def load_rows(path: Path, key: str) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get(key, [])
    return list(rows) if isinstance(rows, list) else []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate11-dir", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end-exclusive", type=date.fromisoformat, required=True)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--evidence-class", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.end_exclusive <= args.start:
        raise ValueError("end-exclusive must be after start")

    candidate_dir = args.candidate11_dir.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(candidate_dir))
    configure_variant(args.variant)

    evaluation_days = (args.end_exclusive - args.start).days
    config = json.loads(
        (candidate_dir / "config.json").read_text(encoding="utf-8"),
    )
    config["selection"]["weeks"]["V56"] = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end_exclusive.isoformat(),
    }
    config["selection"]["evaluation_days"] = evaluation_days
    config["v56_evaluation_contract"] = {
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "frozen_candidate": (
            "v52 funded source-equilibrium external runner with v51 complete "
            "all-cost certificate and v49 rank-one transfer state"
        ),
        "only_variable": (
            "require the candidate's pre-event direction-signed trend to be "
            "negative and its existing trailing directional rank to be in the "
            "Candidate 11 top half"
        ),
        "new_fitted_thresholds": [],
        "risk_fraction": 0.03,
        "continuous_account": evaluation_days > 7,
        "success_claim": False,
    }
    config_path = output / "v56_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V56", output)
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    life_counts = Counter(str(row.get("type", "UNKNOWN")) for row in lifecycle)
    rejections = [
        row
        for row in metrics.get("candidate_rejections", [])
        if row.get("type") == "REVERSAL_OWNERSHIP_REJECTED"
    ]
    metrics.update(
        {
            "evaluation_days": evaluation_days,
            "period_start": args.start.isoformat(),
            "period_end_exclusive": args.end_exclusive.isoformat(),
            "variant": args.variant,
            "evidence_class": args.evidence_class,
            "candidate_generation": (
                "candidate-10-v56-pre-event-reversal-ownership"
            ),
            "v56_reversal_ownership_enabled": (
                os.environ["C10_V56_REVERSAL_OWNERSHIP"] == "1"
            ),
            "v56_reversal_ownership_rejection_count": len(rejections),
            "v56_reversal_ownership_rejection_reasons": dict(Counter(
                str(row.get("reason", "UNKNOWN")) for row in rejections
            )),
            "v56_funded_transition_count": life_counts.get(
                "FUNDED_SOURCE_EQUILIBRIUM_EXTERNAL_RUNNER_SUBMITTED", 0
            ),
            "success_claim": False,
        },
    )
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
