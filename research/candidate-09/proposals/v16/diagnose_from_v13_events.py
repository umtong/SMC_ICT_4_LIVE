#!/usr/bin/env python3
"""Reconstruct the causal v16 opportunity envelope from completed v13 events.

This is a detector diagnostic, not an execution simulation.  It uses only completed
breach/reentry events and source-auction metadata already emitted by Nautilus-backed v13.
No future bar, target touch, stop touch, PnL, or position outcome is read.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

MINUTE_NS = 60_000_000_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in args.events.read_text(encoding="utf-8").splitlines() if line]
    levels = {
        row["scenario_id"].removeprefix("level-"): row["details"]
        for row in rows
        if row["event_type"] == "EXTERNAL_LIQUIDITY_LEVEL_CONFIRMED"
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["run_id"], row["scenario_id"])].append(row)

    flow = config["flow"]
    breach = config["breach"]
    trade = config["trade"]
    cost = float(config["risk"]["composite_taker_cost_per_fill"])
    candidates: list[dict[str, Any]] = []
    immediate_reentries = 0
    all_reentries = 0

    for (run_id, scenario_id), events in grouped.items():
        expired = next((
            row for row in events
            if row["event_type"] == "SCENARIO_EXPIRED"
            and row["reason_code"] == "BREACH_REENTERED_RANGE_BEFORE_ACCEPTANCE"
        ), None)
        breach_event = next((row for row in events if row["event_type"] == "NEUTRAL_LIQUIDITY_BREACH"), None)
        if expired is None or breach_event is None:
            continue
        all_reentries += 1
        if expired["observed_time_ns"] - breach_event["observed_time_ns"] != MINUTE_NS:
            continue
        immediate_reentries += 1

        current = expired["details"]
        first = breach_event["details"]
        direction = str(current["direction"])
        atr = max(float(current["atr"]), 1e-12)
        level_price = float(current["level_price"])
        extreme = (
            max(float(first["high"]), float(current["high"]))
            if direction == "UP"
            else min(float(first["low"]), float(current["low"]))
        )
        excursion_atr = abs(extreme - level_price) / atr
        body_atr = abs(float(current["close"]) - float(current["open"])) / atr
        close_buffer = float(breach["failure_close_buffer_atr"]) * atr
        if direction == "UP":
            rejection = float(current["close"]) <= level_price - close_buffer and float(current["close"]) < float(current["open"])
            residual = float(current["post_flow"]) > 0.0
            side = "SELL"
            stop = max(extreme, float(current["high"])) + float(breach["stop_buffer_atr"]) * atr
        else:
            rejection = float(current["close"]) >= level_price + close_buffer and float(current["close"]) > float(current["open"])
            residual = float(current["post_flow"]) < 0.0
            side = "BUY"
            stop = min(extreme, float(current["low"])) - float(breach["stop_buffer_atr"]) * atr

        match = re.match(r"resolution-([^-]+)-", scenario_id)
        if match is None or match.group(1) not in levels:
            raise RuntimeError(f"missing source-auction metadata for {scenario_id}")
        source = levels[match.group(1)]
        entry = float(current["close"])
        if side == "SELL":
            target = float(source["range_midpoint"]) if float(source["range_midpoint"]) < entry else float(source["range_low"])
            geometry_ok = target < entry < stop
        else:
            target = float(source["range_midpoint"]) if float(source["range_midpoint"]) > entry else float(source["range_high"])
            geometry_ok = stop < entry < target
        net_risk = abs(entry - stop) + cost * entry + cost * stop
        net_reward = abs(target - entry) - cost * entry - cost * target
        net_rr = net_reward / net_risk if geometry_ok and net_risk > 0.0 and net_reward > 0.0 else None

        qualifies = (
            rejection
            and body_atr >= float(flow["minimum_resolution_displacement_atr"])
            and excursion_atr >= float(flow["minimum_excursion_atr"])
            and residual
            and float(current["max_volume_ratio"]) >= float(flow["minimum_volume_ratio"])
            and net_rr is not None
            and net_rr >= float(trade["minimum_net_reward_to_risk"])
        )
        if qualifies:
            candidates.append({
                "run_id": run_id,
                "scenario_id": scenario_id,
                "observed_time_ns": expired["observed_time_ns"],
                "direction": direction,
                "side": side,
                "horizon_minutes": int(current["horizon_minutes"]),
                "sweep_excursion_atr": excursion_atr,
                "rejection_body_atr": body_atr,
                "cumulative_post_flow": float(current["post_flow"]),
                "max_volume_ratio": float(current["max_volume_ratio"]),
                "entry_reference": entry,
                "stop_price": stop,
                "target_price": target,
                "net_reward_to_risk": net_rr,
            })

    result = {
        "classification": "CAUSAL_OPPORTUNITY_DIAGNOSTIC_NOT_BACKTEST",
        "future_information_used": False,
        "pnl_or_target_stop_outcomes_used": False,
        "all_preacceptance_reentries": all_reentries,
        "immediate_one_bar_reentries": immediate_reentries,
        "qualified_opportunities": len(candidates),
        "qualified_by_run": dict(Counter(item["run_id"] for item in candidates)),
        "qualified_by_horizon": {str(k): v for k, v in Counter(item["horizon_minutes"] for item in candidates).items()},
        "qualified_by_direction": dict(Counter(item["direction"] for item in candidates)),
        "candidates": candidates,
    }
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
