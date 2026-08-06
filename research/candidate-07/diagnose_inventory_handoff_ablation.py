#!/usr/bin/env python3
"""Single core-route ablation for the inventory-handoff hypothesis.

The frozen baseline contains two mutually exclusive routes after OI release:
continued-release acceptance and release-to-build reversal.  This ablation
removes only RELEASE_TO_BUILD_REVERSAL.  It does not change data, pools,
thresholds, stops, targets, path accounting or the global one-slot sequence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
from typing import Any

import pandas as pd

import diagnose_inventory_handoff_exit_safe as exit_safe  # applies path fix
import diagnose_inventory_handoff as base
from data_positioning import load_positioning_bundle
from diagnose_failed_flow import aggregate_flow
from diagnose_mtf_liquidity import context_bars, pool_confirmations
from diagnose_session_handoff import _align_positioning
from smc_ict_4.manifest import write_json_atomic


def _summary(scenarios: list[dict[str, Any]], minimum_rr: float) -> dict[str, Any]:
    entry = [item for item in scenarios if item.get("outcome") == "ENTRY_READY"]
    outcomes = Counter(str((item.get("path") or {}).get("outcome")) for item in entry)
    dates = {str(item["confirmation"]["timestamp"])[:10] for item in entry}
    mfe = [float(item["path"]["mfe_r"]) for item in entry if item["path"].get("mfe_r") is not None]
    mae = [float(item["path"]["mae_r"]) for item in entry if item["path"].get("mae_r") is not None]
    return {
        "scenarios": len(scenarios),
        "entry_ready": len(entry),
        "active_days": len(dates),
        "path_outcome_counts": dict(sorted(outcomes.items())),
        "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
        "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
        "median_mae_r": float(pd.Series(mae).median()) if mae else None,
        "gate": {
            "minimum_entry_ready": len(entry) >= 7,
            "minimum_active_days": len(dates) >= 4,
            "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
            "median_mfe_at_least_minimum_rr": bool(mfe) and float(pd.Series(mfe).median()) >= minimum_rr,
            "median_mae_below_one_r": bool(mae) and float(pd.Series(mae).median()) < 1.0,
        },
    }


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = load_positioning_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("inventory_handoff_ablation_data_manifest.json"),
    )
    flow_logic = dict(config["flow_logic"])
    bars = aggregate_flow(
        bundle.frame,
        int(flow_logic["signal_minutes"]),
        int(flow_logic["flow_period"]),
    )
    logic = base.HandoffLogic()
    aligned = _align_positioning(
        bars,
        bundle.metrics,
        oi_period=logic.oi_period,
        oi_impulse_rank=logic.oi_impulse_rank,
    )
    baseline = base.diagnose(
        aligned,
        confirmations=pool_confirmations(context_bars(bundle.frame)),
        trade_start_ns=base._utc_ns(args.start),
        trade_end_ns=base._utc_ns(args.end),
        logic=logic,
    )
    baseline_scenarios = list(baseline["scenarios"])
    ablated_scenarios = [
        item for item in baseline_scenarios
        if str(item.get("route")) != "RELEASE_TO_BUILD_REVERSAL"
    ]
    baseline_summary = _summary(baseline_scenarios, logic.minimum_rr)
    ablation_summary = _summary(ablated_scenarios, logic.minimum_rr)
    baseline_summary["gate"]["passed"] = all(baseline_summary["gate"].values())
    ablation_summary["gate"]["passed"] = all(ablation_summary["gate"].values())
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "ablation": "REMOVE_RELEASE_TO_BUILD_REVERSAL",
        "controlled_variables": {
            "period": {"start": args.start.isoformat(), "end_exclusive": args.end.isoformat()},
            "data": "identical checksum-verified public archives",
            "liquidity_pools": "identical causal 15-minute confirmations",
            "thresholds": {name: getattr(logic, name) for name in logic.__dataclass_fields__},
            "path_accounting": "identical exit-safe first-event truncation",
            "changed_variable": "route availability only",
            "orders_or_pnl": False,
        },
        "baseline_summary": baseline_summary,
        "ablation_summary": ablation_summary,
        "removed_scenarios": [
            item for item in baseline_scenarios
            if str(item.get("route")) == "RELEASE_TO_BUILD_REVERSAL"
        ],
        "surviving_scenarios": ablated_scenarios,
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
