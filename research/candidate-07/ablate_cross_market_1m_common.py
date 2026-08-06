#!/usr/bin/env python3
"""Remove common-price-discovery continuation from the frozen one-minute model.

The baseline is reproduced in the same workflow from identical source, data and
causal pool contracts.  This script changes only route availability: it retains
FUTURES_ONLY_OVERSHOOT and removes COMMON_PRICE_DISCOVERY.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any

import pandas as pd

from smc_ict_4.manifest import write_json_atomic


def summarize(scenarios: list[dict[str, Any]], minimum_rr: float) -> dict[str, Any]:
    entries = [item for item in scenarios if item.get("outcome") == "ENTRY_READY"]
    outcomes = Counter(str((item.get("path") or {}).get("outcome")) for item in entries)
    active_dates = {str(item["confirmation"]["timestamp"])[:10] for item in entries}
    branches: dict[str, Counter[str]] = defaultdict(Counter)
    for item in scenarios:
        branches[str(item.get("branch", "UNROUTED"))][str(item.get("outcome"))] += 1
    mfe = [float(item["path"]["mfe_r"]) for item in entries if item["path"].get("mfe_r") is not None]
    mae = [float(item["path"]["mae_r"]) for item in entries if item["path"].get("mae_r") is not None]
    gate = {
        "minimum_entry_ready": len(entries) >= 7,
        "minimum_active_days": len(active_dates) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "median_mfe_at_least_minimum_rr": bool(mfe) and float(pd.Series(mfe).median()) >= minimum_rr,
        "median_mae_below_one_r": bool(mae) and float(pd.Series(mae).median()) < 1.0,
    }
    gate["passed"] = all(gate.values())
    return {
        "scenarios": len(scenarios),
        "entry_ready": len(entries),
        "active_days": len(active_dates),
        "path_outcome_counts": dict(sorted(outcomes.items())),
        "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
        "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
        "median_mae_r": float(pd.Series(mae).median()) if mae else None,
        "by_branch": {
            branch: dict(sorted(counts.items()))
            for branch, counts in sorted(branches.items())
        },
        "diagnostic_gate": gate,
    }


def run(args: argparse.Namespace) -> int:
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    expected = "one-minute cross-market liquidation transfer with completed five-minute OI state"
    if baseline.get("hypothesis") != expected:
        raise ValueError("unexpected baseline hypothesis")
    scenarios = list(baseline.get("scenarios") or [])
    surviving = [item for item in scenarios if str(item.get("branch")) != "COMMON_PRICE_DISCOVERY"]
    removed = [item for item in scenarios if str(item.get("branch")) == "COMMON_PRICE_DISCOVERY"]
    minimum_rr = float((baseline.get("logic") or {})["minimum_rr"])
    payload = {
        "candidate": "candidate-07",
        "stage": "week-1-ablation",
        "ablation": "REMOVE_COMMON_PRICE_DISCOVERY_CONTINUATION",
        "controlled_variables": {
            "baseline_source": str(args.baseline),
            "period": baseline.get("period"),
            "logic": baseline.get("logic"),
            "data_contract": baseline.get("data_contract"),
            "changed_variable": "route availability only",
            "orders_or_pnl": False,
        },
        "baseline_summary": baseline.get("summary"),
        "ablation_summary": summarize(surviving, minimum_rr),
        "removed_scenarios": removed,
        "surviving_scenarios": surviving,
    }
    write_json_atomic(args.output.resolve(), payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
