#!/usr/bin/env python3
"""Run v52 equilibrium-primary versus independent external-runner contracts."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "source-equilibrium-primary",
    "external-runner-full-risk",
    "external-runner-funded-at-equilibrium",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v52 variant: {variant}")
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
    os.environ["C10_V52_EXTERNAL_RUNNER"] = (
        "0" if variant == "source-equilibrium-primary" else "1"
    )
    os.environ["C10_V52_FUNDED_EQUILIBRIUM"] = (
        "1" if variant == "external-runner-funded-at-equilibrium" else "0"
    )


def count_jsonl(path: Path, key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if not path.is_file():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            counts[str(json.loads(line).get(key, "UNKNOWN"))] += 1
    return dict(counts)


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
    config["selection"]["weeks"]["V52"] = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end_exclusive.isoformat(),
    }
    config["selection"]["evaluation_days"] = evaluation_days
    config["v52_evaluation_contract"] = {
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "frozen_detector": "v40 decoupled source-range failed auction",
        "frozen_entry": "v41 first-displacement near-edge passive retrace",
        "frozen_hard_stop_and_sizing": (
            "source raid extreme plus existing ATR buffer and current-NAV 3% "
            "complete all-cost loss budget"
        ),
        "frozen_market_router": (
            "v47 event-direction rank one plus v49 distributed/pioneer transfer"
        ),
        "frozen_preorder_cost_validity": (
            "v51 entry/target and entry/stop size-dependent impact certificate"
        ),
        "first_delivery_checkpoint": "source dealing-range equilibrium",
        "external_runner": (
            "strictly preexisting same-direction Candidate 11 external hazard "
            "selected with the existing hazard and dominance equations"
        ),
        "funded_transition": (
            "minimum solved equilibrium partial whose all-cost locked profit "
            "covers the residual original-stop loss"
        ),
        "partial_fraction": "solved, never fixed",
        "new_fitted_thresholds": [],
        "risk_fraction": 0.03,
        "continuous_account": evaluation_days > 7,
        "success_claim": False,
    }
    config_path = output / "v52_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V52", output)
    plans = load_rows(output / "submitted_plans.json", "plans")
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    runner_plans = []
    for row in plans:
        details = row.get("details", {})
        if not isinstance(details, dict):
            continue
        runner = details.get("external_runner")
        economics = details.get("size_dependent_all_cost_economics")
        if isinstance(runner, dict):
            runner_plans.append({
                "scenario_id": row.get("scenario_id"),
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "entry": row.get("entry"),
                "stop": row.get("stop"),
                "target": row.get("target"),
                "source_equilibrium_checkpoint": details.get(
                    "source_equilibrium_checkpoint"
                ),
                "external_runner": runner,
                "all_cost_economics": economics,
            })
    runner_rejections = [
        row
        for row in metrics.get("candidate_rejections", [])
        if row.get("type") == "EXTERNAL_RUNNER_REJECTED"
    ]
    reward_rejections = [
        row
        for row in metrics.get("candidate_rejections", [])
        if str(row.get("type", "")).startswith("SIZE_DEPENDENT_REWARD_REJECTED")
    ]
    funded = [
        row
        for row in lifecycle
        if row.get("type")
        == "FUNDED_SOURCE_EQUILIBRIUM_EXTERNAL_RUNNER_SUBMITTED"
    ]
    life_counts = Counter(str(row.get("type", "UNKNOWN")) for row in lifecycle)
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics.update(
        {
            "evaluation_days": evaluation_days,
            "period_start": args.start.isoformat(),
            "period_end_exclusive": args.end_exclusive.isoformat(),
            "variant": args.variant,
            "evidence_class": args.evidence_class,
            "candidate_generation": (
                "candidate-10-v52-funded-source-equilibrium-external-runner"
            ),
            "v52_external_runner_enabled": (
                os.environ["C10_V52_EXTERNAL_RUNNER"] == "1"
            ),
            "v52_funded_equilibrium_enabled": (
                os.environ["C10_V52_FUNDED_EQUILIBRIUM"] == "1"
            ),
            "v52_external_runner_plan_count": len(runner_plans),
            "v52_external_runner_plans": runner_plans,
            "v52_external_runner_rejection_count": len(runner_rejections),
            "v52_external_runner_rejection_reasons": dict(Counter(
                str(row.get("reason", "UNKNOWN"))
                for row in runner_rejections
            )),
            "v52_reward_rejection_count": len(reward_rejections),
            "v52_reward_rejection_reasons": dict(Counter(
                str(row.get("reason", "UNKNOWN"))
                for row in reward_rejections
            )),
            "v52_funded_transition_count": len(funded),
            "v52_funded_transitions": funded,
            "v52_pending_equilibrium_cancellation_count": life_counts.get(
                "PENDING_ENTRY_CANCELED_AFTER_EQUILIBRIUM_DELIVERY", 0
            ),
            "v52_state_event_counts": {
                name: events.get(name, 0)
                for name in (
                    "SOURCE_RANGE_LIQUIDITY_SWEEP",
                    "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
                    "TRADE_PLAN_CONFIRMED",
                    "ENTRY_FILLED",
                    "POSITION_TERMINAL",
                )
            },
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
