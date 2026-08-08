#!/usr/bin/env python3
"""Run v43 funded microstructure risk-transfer exact ablation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "near-edge-original-stop",
    "near-edge-funded-micro-reduction",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v43 variant: {variant}")
    os.environ["C10_V27_ABLATE_LEADERSHIP"] = "0"
    os.environ["C10_V28_ABLATE_RESOLUTION"] = "0"
    os.environ["C10_V29_ABLATE_EXTERNAL_DRAW"] = "1"
    os.environ["C10_V40_SOURCE_EQUILIBRIUM_DETECTOR"] = "1"
    os.environ["C10_V36_CE_REJECTION"] = "0"
    os.environ["C10_V36_EQUILIBRIUM_TARGET"] = "1"
    os.environ["C10_V41_SOURCE_ENTRY_MODE"] = "FIRST_DISPLACEMENT_NEAR_EDGE"
    os.environ["C10_V37_INTERNAL_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_PROTECTION"] = "0"
    os.environ["C10_V38_MICRO_PIVOT_REFERENCE"] = "EXPECTED_ENTRY"
    os.environ["C10_V43_FUNDED_MICRO_REDUCTION"] = (
        "1" if variant.endswith("funded-micro-reduction") else "0"
    )


def count_jsonl(path: Path, key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if not path.is_file():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            counts[str(json.loads(line).get(key, "UNKNOWN"))] += 1
    return dict(counts)


def lifecycle_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(Counter(str(row.get("type", "UNKNOWN")) for row in value.get("events", [])))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate11-dir", type=Path, required=True)
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidate_dir = args.candidate11_dir.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(candidate_dir))
    configure_variant(args.variant)

    config = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    config["selection"]["weeks"]["V43"] = {
        "start": args.week_start.isoformat(),
        "end_exclusive": (args.week_start + timedelta(days=7)).isoformat(),
    }
    config["selection"]["evaluation_days"] = 7
    config["v43_evaluation_contract"] = {
        "variant": args.variant,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "frozen_detector": "v40 decoupled source-range failed auction",
        "frozen_entry": "v41 first-displacement near-edge passive retrace",
        "frozen_primary_target": "source dealing-range equilibrium",
        "frozen_initial_invalidation": "source raid extreme plus frozen ATR buffer",
        "only_ablation_variable": (
            "after a defended one-minute profitable-side pivot, reduce the "
            "minimum solved quantity only when its modeled all-cost profit "
            "funds the complete original-stop loss of the residual"
        ),
        "partial_fraction": "solved, never fixed",
        "residual_target": "original source equilibrium",
        "residual_stop": "original source raid invalidation",
        "hard_pivot_stop": False,
        "risk_fraction": 0.03,
        "success_claim": False,
    }
    config_path = output / "v43_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V43", output)
    metrics.update(
        {
            "week_start": args.week_start.isoformat(),
            "variant": args.variant,
            "candidate_generation": (
                "candidate-10-v43-funded-microstructure-risk-transfer"
            ),
            "v43_funded_micro_reduction_enabled": (
                os.environ["C10_V43_FUNDED_MICRO_REDUCTION"] == "1"
            ),
        },
    )
    records = list(metrics.get("cost_records", []))
    metrics["v43_funded_reduction_armed_count"] = sum(
        bool(row.get("funded_micro_reduction_armed")) for row in records
    )
    metrics["v43_funded_reductions"] = [
        {
            "scenario_id": row.get("scenario_id"),
            "symbol": row.get("symbol"),
            "pivot_event_ts_ns": row.get("funded_micro_pivot_event_ts_ns"),
            "pivot_known_ts_ns": row.get("funded_micro_pivot_known_ts_ns"),
            "pivot_level": row.get("funded_micro_pivot_level"),
            "observed_ts_ns": row.get("funded_micro_observed_ts_ns"),
            "current_price": row.get("funded_micro_current_price"),
            "partial_fraction": row.get("funded_partial_fraction"),
            "partial_quantity": row.get("funded_partial_quantity"),
            "residual_quantity": row.get("funded_residual_quantity"),
            "locked_profit": row.get("funded_locked_profit"),
            "residual_max_loss": row.get("funded_residual_max_loss"),
        }
        for row in records
        if row.get("funded_micro_reduction_armed")
    ]
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    metrics["v43_state_event_counts"] = {
        name: events.get(name, 0)
        for name in (
            "SOURCE_RANGE_LIQUIDITY_SWEEP",
            "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
            "TRADE_PLAN_CONFIRMED",
            "ENTRY_FILLED",
            "FUNDED_MICRO_RISK_TRANSFER_CONFIRMED",
            "POSITION_TERMINAL",
        )
    }
    life = lifecycle_counts(output / "order_lifecycle.json")
    metrics["v43_lifecycle_counts"] = {
        name: life.get(name, 0)
        for name in (
            "FUNDED_MICRO_RISK_TRANSFER_SUBMITTED",
            "MODELED_IMPACT_DEBITED",
            "V43_FUNDED_REPLACEMENT_SUBMISSION_EXCEPTION",
            "PROTECTIVE_ACTIVATION_FAIL_CLOSE_SUBMITTED",
        )
    }
    metrics["success_claim"] = False
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
