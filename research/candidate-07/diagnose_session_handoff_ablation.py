#!/usr/bin/env python3
"""Single-variable ablation for the session-handoff hypothesis.

The baseline diagnostic contains reversal routes and a new-inventory acceptance
continuation route.  This controlled ablation removes only the continuation
route, reruns the same frozen interval with identical data and thresholds, and
reports the surviving reversal evidence.  It creates no orders or hypothetical
NAV.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from data_positioning import load_positioning_bundle
from diagnose_failed_flow import aggregate_flow
from diagnose_session_handoff import (
    SESSIONS,
    _align_positioning,
    _read_json,
    _summary,
    diagnose,
)
from smc_ict_4.manifest import write_json_atomic


def run(args: argparse.Namespace) -> int:
    config = _read_json(args.config.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = load_positioning_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("session_handoff_ablation_data_manifest.json"),
    )
    flow_logic = dict(config["flow_logic"])
    bars = aggregate_flow(
        bundle.frame,
        int(flow_logic["signal_minutes"]),
        int(flow_logic["flow_period"]),
    )
    logic = {
        "contact_min_atr": 0.05,
        "contact_max_atr": 1.50,
        "attack_imbalance": 0.08,
        "flow_z": 0.25,
        "reclaim_buffer_atr": 0.02,
        "acceptance_buffer_atr": 0.05,
        "rejection_wick_fraction": 0.20,
        "acceptance_close_location": 0.65,
        "acceptance_body_fraction": 0.12,
        "confirm_imbalance": 0.02,
        "confirm_body_atr": 0.15,
        "stop_buffer_atr": 0.10,
        "minimum_rr": 1.25,
        "maximum_rr": 3.00,
        "oi_period": 36,
        "oi_impulse_rank": 0.50,
    }
    aligned = _align_positioning(
        bars,
        bundle.metrics,
        oi_period=int(logic["oi_period"]),
        oi_impulse_rank=float(logic["oi_impulse_rank"]),
    )
    baseline = diagnose(
        aligned,
        trade_start=args.start,
        trade_end=args.end,
        logic=logic,
    )
    ablated = [
        item for item in baseline
        if str(item.get("branch")) != "NEW_INVENTORY_ACCEPTANCE"
    ]
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "ablation": "REMOVE_NEW_INVENTORY_ACCEPTANCE_CONTINUATION",
        "controlled_variables": {
            "period": {"start": args.start.isoformat(), "end_exclusive": args.end.isoformat()},
            "sessions": [item.name for item in SESSIONS],
            "market_data": "identical checksum-verified Binance USD-M archives",
            "thresholds": logic,
            "changed_variable": "route availability only",
            "orders_or_pnl": False,
        },
        "baseline_summary": _summary(baseline),
        "ablation_summary": _summary(ablated),
        "removed_scenarios": [
            item for item in baseline
            if str(item.get("branch")) == "NEW_INVENTORY_ACCEPTANCE"
        ],
        "surviving_scenarios": ablated,
    }
    write_json_atomic(output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--stage", default="week-1-ablation")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path(".research-data/candidate-07"))
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
