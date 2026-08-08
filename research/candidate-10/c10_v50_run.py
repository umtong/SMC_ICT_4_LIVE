#!/usr/bin/env python3
"""Run v50 source versus internal dealing-range opportunity expansion."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "source-ranges-transfer-state",
    "internal-ranges-event-leader",
    "internal-ranges-transfer-state",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v50 variant: {variant}")
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
    os.environ["C10_V50_INTERNAL_DEALING_RANGE"] = (
        "0" if variant.startswith("source-ranges") else "1"
    )
    os.environ["C10_V49_TRANSFER_STATE_ROUTER"] = (
        "1" if variant.endswith("transfer-state") else "0"
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
    config["selection"]["weeks"]["V50"] = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end_exclusive.isoformat(),
    }
    config["selection"]["evaluation_days"] = evaluation_days
    config["v50_evaluation_contract"] = {
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "candidate11_source_commit": "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327",
        "frozen_failed_auction": (
            "boundary sweep, reclaim, post-sweep internal structure break, "
            "directional displacement body and aggressor flow"
        ),
        "frozen_entry": "first-displacement near-edge passive retrace",
        "frozen_target": "paired dealing-range equilibrium",
        "frozen_hard_stop_and_sizing": (
            "sweep extreme plus existing ATR buffer and current-NAV 3% "
            "all-cost loss budget"
        ),
        "frozen_rank_router": "event-direction rank one",
        "internal_range_source": (
            "right-confirmed five-minute pivot paired with the latest older "
            "opposite right-confirmed five-minute pivot"
        ),
        "internal_range_lifetime": "existing event_expiry_bars",
        "internal_range_pivot_wing": "existing internal_pivot_wing",
        "transfer_router": (
            "v49 distributed-versus-pioneer transfer state"
            if args.variant.endswith("transfer-state")
            else "disabled exact ablation"
        ),
        "new_fitted_thresholds": [],
        "risk_multiplier": "none",
        "risk_fraction": 0.03,
        "continuous_account": evaluation_days > 7,
        "success_claim": False,
    }
    config_path = output / "v50_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V50", output)
    events = count_jsonl(output / "scenario_events.raw.jsonl", "event_type")
    plans = load_rows(output / "submitted_plans.json", "plans")
    internal_plans = [
        row
        for row in plans
        if isinstance(row.get("details"), dict)
        and row["details"].get("pool_source")
        == "CONFIRMED_INTERNAL_5M_DEALING_RANGE"
    ]
    source_plans = [row for row in plans if row not in internal_plans]
    transfer_rejections = [
        row
        for row in metrics.get("candidate_rejections", [])
        if row.get("type") == "EVENT_TRANSFER_STATE_REJECTED"
    ]
    event_rank_rejections = [
        row
        for row in metrics.get("candidate_rejections", [])
        if row.get("type") == "EVENT_DIRECTION_LEADER_REJECTED"
    ]
    metrics.update(
        {
            "evaluation_days": evaluation_days,
            "period_start": args.start.isoformat(),
            "period_end_exclusive": args.end_exclusive.isoformat(),
            "variant": args.variant,
            "evidence_class": args.evidence_class,
            "candidate_generation": (
                "candidate-10-v50-internal-dealing-range-failed-auction"
            ),
            "v50_internal_dealing_range_enabled": (
                os.environ["C10_V50_INTERNAL_DEALING_RANGE"] == "1"
            ),
            "v50_transfer_state_router_enabled": (
                os.environ["C10_V49_TRANSFER_STATE_ROUTER"] == "1"
            ),
            "v50_internal_endpoint_confirmed_count": events.get(
                "INTERNAL_DEALING_RANGE_ENDPOINT_CONFIRMED", 0
            ),
            "v50_source_sweep_count": events.get(
                "SOURCE_RANGE_LIQUIDITY_SWEEP", 0
            ),
            "v50_failed_auction_confirmation_count": events.get(
                "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED", 0
            ),
            "v50_trade_plan_count": events.get("TRADE_PLAN_CONFIRMED", 0),
            "v50_entry_fill_count": events.get("ENTRY_FILLED", 0),
            "v50_internal_submitted_plan_count": len(internal_plans),
            "v50_source_submitted_plan_count": len(source_plans),
            "v50_transfer_rejection_count": len(transfer_rejections),
            "v50_transfer_rejection_reasons": dict(Counter(
                str(row.get("reason", "UNKNOWN"))
                for row in transfer_rejections
            )),
            "v50_event_rank_rejection_count": len(event_rank_rejections),
            "v50_event_rank_rejection_reasons": dict(Counter(
                str(row.get("reason", "UNKNOWN"))
                for row in event_rank_rejections
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
