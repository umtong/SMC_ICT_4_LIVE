#!/usr/bin/env python3
"""Run v57 source-equilibrium versus earliest solvable funding checkpoint."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "source-equilibrium-funding-checkpoint",
    "internal-liquidity-funding-checkpoint",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v57 variant: {variant}")
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
    os.environ["C10_V56_REVERSAL_OWNERSHIP"] = "0"
    os.environ["C10_V57_INTERNAL_FUNDING_CHECKPOINT"] = (
        "1" if variant.startswith("internal-liquidity") else "0"
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
    config["selection"]["weeks"]["V57"] = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end_exclusive.isoformat(),
    }
    config["selection"]["evaluation_days"] = evaluation_days
    config["v57_evaluation_contract"] = {
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "frozen_candidate": (
            "v52 rank-one transfer-state external runner with v51 complete "
            "all-cost economics and solved residual-risk funding"
        ),
        "only_variable": (
            "fund at source equilibrium or at the earliest live preconfirmed "
            "five-minute internal liquidity where the exact submitted size can "
            "fund the residual original-stop loss"
        ),
        "target": "unchanged independent external draw",
        "partial_fraction": "solved, never fixed",
        "source_equilibrium_fallback": True,
        "new_fitted_thresholds": [],
        "risk_fraction": 0.03,
        "continuous_account": evaluation_days > 7,
        "success_claim": False,
    }
    config_path = output / "v57_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V57", output)
    plans = load_rows(output / "submitted_plans.json", "plans")
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    selections = []
    for row in plans:
        details = row.get("details", {})
        if not isinstance(details, dict):
            continue
        checkpoint = details.get("funded_checkpoint")
        if isinstance(checkpoint, dict):
            selections.append({
                "scenario_id": row.get("scenario_id"),
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "selected_level": checkpoint.get("selected_level"),
                "selected_source": checkpoint.get("selected_source"),
                "source_equilibrium": checkpoint.get("source_equilibrium"),
                "expected_partial_fraction": checkpoint.get(
                    "selected_expected_partial_fraction"
                ),
                "candidate_count": checkpoint.get(
                    "candidate_count_before_source_equilibrium"
                ),
            })
    funded = [
        row
        for row in lifecycle
        if row.get("type")
        == "FUNDED_DELIVERY_CHECKPOINT_EXTERNAL_RUNNER_SUBMITTED"
    ]
    rejections = [
        row
        for row in metrics.get("candidate_rejections", [])
        if str(row.get("type", "")).startswith("FUNDED_CHECKPOINT_REJECTED")
    ]
    metrics.update(
        {
            "evaluation_days": evaluation_days,
            "period_start": args.start.isoformat(),
            "period_end_exclusive": args.end_exclusive.isoformat(),
            "variant": args.variant,
            "evidence_class": args.evidence_class,
            "candidate_generation": (
                "candidate-10-v57-earliest-solvable-funding-checkpoint"
            ),
            "v57_internal_checkpoint_enabled": (
                os.environ["C10_V57_INTERNAL_FUNDING_CHECKPOINT"] == "1"
            ),
            "v57_checkpoint_selection_count": len(selections),
            "v57_checkpoint_source_counts": dict(Counter(
                str(row.get("selected_source", "UNKNOWN"))
                for row in selections
            )),
            "v57_checkpoint_selections": selections,
            "v57_funded_transition_count": len(funded),
            "v57_funded_transitions": funded,
            "v57_checkpoint_rejection_count": len(rejections),
            "v57_checkpoint_rejection_reasons": dict(Counter(
                str(row.get("reason", "UNKNOWN")) for row in rejections
            )),
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
