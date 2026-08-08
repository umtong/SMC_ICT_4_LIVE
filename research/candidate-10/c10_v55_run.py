#!/usr/bin/env python3
"""Run v55 3x2 market-router by pre-checkpoint failure factorial."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys

ROUTERS = ("leadership", "event-rank-one", "transfer-state")
FAILURES = ("hard-stop", "void-failure")
VARIANTS = tuple(f"{router}-{failure}" for router in ROUTERS for failure in FAILURES)


def configure_variant(variant: str) -> tuple[str, str]:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v55 variant: {variant}")
    router, failure = next(
        (router, failure)
        for router in ROUTERS
        for failure in FAILURES
        if variant == f"{router}-{failure}"
    )
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
    os.environ["C10_V50_INTERNAL_DEALING_RANGE"] = "0"
    os.environ["C10_V51_SIZE_DEPENDENT_REWARD"] = "1"
    os.environ["C10_V52_EXTERNAL_RUNNER"] = "1"
    os.environ["C10_V52_FUNDED_EQUILIBRIUM"] = "1"
    os.environ["C10_V46_VOID_CLOSE_EXIT"] = (
        "1" if failure == "void-failure" else "0"
    )
    os.environ["C10_V47_EVENT_LEADER_ONLY"] = (
        "0" if router == "leadership" else "1"
    )
    os.environ["C10_V49_TRANSFER_STATE_ROUTER"] = (
        "1" if router == "transfer-state" else "0"
    )
    return router, failure


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
    router, failure = configure_variant(args.variant)

    evaluation_days = (args.end_exclusive - args.start).days
    config = json.loads(
        (candidate_dir / "config.json").read_text(encoding="utf-8"),
    )
    config["selection"]["weeks"]["V55"] = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end_exclusive.isoformat(),
    }
    config["selection"]["evaluation_days"] = evaluation_days
    config["v55_evaluation_contract"] = {
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "frozen_trade": (
            "v40 source failed auction, v41 near-edge entry, v51 complete "
            "all-cost certificate and v52 solved source-equilibrium funded "
            "independent external runner"
        ),
        "factor_market_router": router,
        "factor_precheckpoint_failure": failure,
        "router_levels": {
            "leadership": "frozen Candidate 11 causal leadership gate",
            "event-rank-one": "candidate event direction rank exactly one",
            "transfer-state": "rank one plus v49 distributed/pioneer state",
        },
        "failure_levels": {
            "hard-stop": "source-raid hard stop only before checkpoint",
            "void-failure": (
                "completed close through first-displacement void before "
                "source-equilibrium checkpoint"
            ),
        },
        "new_fitted_thresholds": [],
        "risk_fraction": 0.03,
        "continuous_account": evaluation_days > 7,
        "success_claim": False,
    }
    config_path = output / "v55_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V55", output)
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    life_counts = Counter(str(row.get("type", "UNKNOWN")) for row in lifecycle)
    rejections = list(metrics.get("candidate_rejections", []))
    metrics.update(
        {
            "evaluation_days": evaluation_days,
            "period_start": args.start.isoformat(),
            "period_end_exclusive": args.end_exclusive.isoformat(),
            "variant": args.variant,
            "evidence_class": args.evidence_class,
            "candidate_generation": (
                "candidate-10-v55-router-by-failure-factorial"
            ),
            "v55_market_router": router,
            "v55_precheckpoint_failure": failure,
            "v55_funded_transition_count": life_counts.get(
                "FUNDED_SOURCE_EQUILIBRIUM_EXTERNAL_RUNNER_SUBMITTED", 0
            ),
            "v55_void_failure_exit_count": life_counts.get(
                "FIRST_DISPLACEMENT_VOID_CLOSE_FAILURE_EXIT_SUBMITTED", 0
            ),
            "v55_rejection_type_counts": dict(Counter(
                str(row.get("type", "UNKNOWN")) for row in rejections
            )),
            "v55_rejection_reason_counts": dict(Counter(
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
