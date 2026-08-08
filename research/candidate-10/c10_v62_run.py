#!/usr/bin/env python3
"""Run v62 event-leader versus isolated extreme-transfer continuously."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys

VARIANTS = (
    "event-leader-baseline",
    "isolated-extreme-transfer",
)


def configure_variant(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unsupported v62 variant: {variant}")
    fixed = {
        "C10_V27_ABLATE_LEADERSHIP": "0",
        "C10_V28_ABLATE_RESOLUTION": "0",
        "C10_V29_ABLATE_EXTERNAL_DRAW": "1",
        "C10_V40_SOURCE_EQUILIBRIUM_DETECTOR": "1",
        "C10_V36_CE_REJECTION": "0",
        "C10_V36_EQUILIBRIUM_TARGET": "1",
        "C10_V41_SOURCE_ENTRY_MODE": "FIRST_DISPLACEMENT_NEAR_EDGE",
        "C10_V44_PRIMARY_TARGET_MODE": "SOURCE_EQUILIBRIUM",
        "C10_V45_INVALIDATION_MODE": "SOURCE_RAID_EXTREME",
        "C10_V37_INTERNAL_PIVOT_PROTECTION": "0",
        "C10_V38_MICRO_PIVOT_PROTECTION": "0",
        "C10_V38_MICRO_PIVOT_REFERENCE": "EXPECTED_ENTRY",
        "C10_V46_VOID_CLOSE_EXIT": "0",
        "C10_V47_EVENT_LEADER_ONLY": "1",
        "C10_V62_ISOLATED_EXTREME_TRANSFER": (
            "1" if variant == "isolated-extreme-transfer" else "0"
        ),
    }
    os.environ.update(fixed)


def load_rows(path: Path, key: str) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get(key, [])
    return list(rows) if isinstance(rows, list) else []


def count_jsonl(path: Path, key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                counts[str(json.loads(line).get(key, "UNKNOWN"))] += 1
    return dict(counts)


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
    config["selection"]["weeks"]["V62"] = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end_exclusive.isoformat(),
    }
    config["selection"]["evaluation_days"] = evaluation_days
    config["v62_evaluation_contract"] = {
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "candidate11_source_commit": (
            "f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327"
        ),
        "account_contract": (
            "one continuous NautilusTrader account with zero interval-internal "
            "NAV resets"
        ),
        "frozen_detector": "v40 decoupled source-range failed auction",
        "frozen_entry": "v41 first-displacement near-edge passive retrace",
        "frozen_target": "source dealing-range equilibrium",
        "frozen_hard_stop_and_sizing": (
            "source raid extreme plus frozen ATR buffer; current all-cost NAV "
            "times 3 percent maximum planned loss"
        ),
        "frozen_first_router": "candidate event-direction rank equals one",
        "only_alpha_variable": (
            "complete isolated extreme-transfer state: peer median nonpositive "
            "and trailing rank either one or synchronized market count"
        ),
        "new_fitted_thresholds": [],
        "risk_multiplier": "none",
        "success_claim": False,
    }
    config_path = output / "v62_config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from run_leadership_scdam import run

    metrics = run(config_path, "V62", output)
    plans = load_rows(output / "submitted_plans.json", "plans")
    lifecycle = load_rows(output / "order_lifecycle.json", "events")
    states: Counter[str] = Counter()
    accepted_details: list[dict[str, object]] = []
    for row in plans:
        details = row.get("details", {})
        if not isinstance(details, dict):
            continue
        leadership = details.get("market_leadership", {})
        router = details.get("isolated_extreme_transfer_router", {})
        leadership = leadership if isinstance(leadership, dict) else {}
        router = router if isinstance(router, dict) else {}
        state = str(router.get("selected_state", "MISSING"))
        states[state] += 1
        accepted_details.append({
            "scenario_id": row.get("scenario_id"),
            "symbol": row.get("symbol"),
            "direction": row.get("direction"),
            "observed_ts_ns": row.get("observed_ts_ns"),
            "selected_state": state,
            "trailing_direction_rank": leadership.get(
                "trailing_direction_rank"
            ),
            "event_direction_rank": leadership.get("event_direction_rank"),
            "peer_event_median": leadership.get("peer_event_median"),
            "candidate_event_move": leadership.get("candidate_event_move"),
            "event_path_efficiency": leadership.get("event_path_efficiency"),
            "event_standardized_displacement": leadership.get(
                "event_standardized_displacement"
            ),
            "confirmation_impulse": leadership.get("confirmation_impulse"),
            "entry": row.get("entry"),
            "stop": row.get("stop"),
            "target": row.get("target"),
            "net_r": row.get("net_r"),
        })

    rejections = [
        row for row in metrics.get("candidate_rejections", [])
        if row.get("type") == "ISOLATED_EXTREME_TRANSFER_REJECTED"
    ]
    raw_counts = count_jsonl(
        output / "scenario_events.raw.jsonl",
        "event_type",
    )
    metrics.update({
        "evaluation_days": evaluation_days,
        "period_start": args.start.isoformat(),
        "period_end_exclusive": args.end_exclusive.isoformat(),
        "variant": args.variant,
        "evidence_class": args.evidence_class,
        "candidate_generation": (
            "candidate-10-v62-isolated-extreme-transfer-router"
        ),
        "v62_continuous_account": True,
        "v62_nav_reset_count": 0,
        "v62_router_enabled": (
            os.environ["C10_V62_ISOLATED_EXTREME_TRANSFER"] == "1"
        ),
        "v62_accepted_state_counts": dict(states),
        "v62_accepted_details": accepted_details,
        "v62_rejection_count": len(rejections),
        "v62_rejection_reasons": dict(Counter(
            str(row.get("reason", "UNKNOWN")) for row in rejections
        )),
        "v62_lifecycle_counts": dict(Counter(
            str(row.get("type", "UNKNOWN")) for row in lifecycle
        )),
        "v62_state_event_counts": {
            name: raw_counts.get(name, 0)
            for name in (
                "SOURCE_RANGE_LIQUIDITY_SWEEP",
                "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
                "TRADE_PLAN_CONFIRMED",
                "ENTRY_FILLED",
                "POSITION_TERMINAL",
            )
        },
        "success_claim": False,
    })
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print("RESULT_JSON=" + json.dumps(metrics, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
