#!/usr/bin/env python3
"""V58c: require both price and executed effort to be non-climactic.

A parent-auction resumption must not need more directional executed effort than
the liquidation impulse it is supposed to resume.  V58 compared only price
movement and admitted a recross whose directional effort was 2.64x the original
break.  This module keeps every V58b state, target, stop and timing rule, but
rejects liquidation-reentry failures when completed recross effort exceeds the
original break effort.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any

import micro_auction_parent_state_router as base
import micro_auction_parent_state_router_v2 as v2


MAX_RECROSS_EFFORT_RATIO = 1.0


def finite(value: Any) -> float:
    return base.finite(value)


def route_signals(
    signals: list[dict[str, Any]],
    rich,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    routed, prior_summary = v2.route_signals(signals, rich)
    kept: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    scenario_counts: Counter[str] = Counter()
    for signal in routed:
        if signal["scenario"] == base.LIQUIDATION_REENTRY_FAILURE:
            index = int(signal["signal_index"])
            side = int(signal["side"])
            details = dict(signal.get("details") or {})
            break_effort = finite(details.get("break_effort"))
            flow = side * finite(rich["flow_60s"].iloc[index])
            notional = finite(rich["notional_60s"].iloc[index])
            recross_effort = max(flow, 0.0) * notional
            if not (
                math.isfinite(break_effort)
                and break_effort > 0.0
                and math.isfinite(recross_effort)
            ):
                counts["liquidation_recross_missing_effort"] += 1
                continue
            effort_ratio = recross_effort / break_effort
            if effort_ratio > MAX_RECROSS_EFFORT_RATIO:
                counts["liquidation_recross_more_climactic_than_break"] += 1
                continue
            details["liquidation_reentry_failure_directional_effort"] = recross_effort
            details["liquidation_reentry_failure_effort_ratio"] = effort_ratio
            details["maximum_non_climactic_effort_ratio"] = (
                MAX_RECROSS_EFFORT_RATIO
            )
            details["v58c_effort_contract"] = (
                "completed recross directional effort cannot exceed original "
                "liquidation-break effort"
            )
            signal = dict(signal)
            signal["details"] = details
            counts["non_climactic_effort_continuation"] += 1
        else:
            counts["unchanged_trapped_inventory_route"] += 1
        kept.append(signal)
        scenario_counts[str(signal["scenario"])] += 1
    summary = dict(prior_summary)
    summary.update(
        {
            "candidate": "candidate-04-v58c-parent-state-micro-auction",
            "compiler": "candidate-04-v58c-parent-state-router-v3",
            "v58c_counts": dict(counts),
            "scenario_counts": dict(scenario_counts),
            "written_signals": len(kept),
            "maximum_non_climactic_effort_ratio": MAX_RECROSS_EFFORT_RATIO,
        }
    )
    return kept, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()
    raw = json.loads(args.signals.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError("signals must contain a JSON list")
    rich = base.load_rich(args.rich_dir, args.symbol)
    routed, summary = route_signals(
        [dict(item) for item in raw if isinstance(item, dict)], rich
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "signals.json").write_text(
        json.dumps(routed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
