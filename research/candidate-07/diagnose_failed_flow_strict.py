#!/usr/bin/env python3
"""Apply strict causal acceptance rules to failed-flow diagnostics.

This post-processor intentionally does not create orders or hypothetical PnL.
It rejects any late re-break which occurs after a completed close has reclaimed
the original consumed liquidity pool, and reports trigger and hold bars
separately.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any

from smc_ict_4.manifest import write_json_atomic


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object in {path}")
    return payload


def is_pool_reclaimed(row: dict[str, Any], *, direction: str, pool: float) -> bool:
    return float(row["close"]) <= pool if direction == "LONG" else float(row["close"]) >= pool


def is_trigger(
    row: dict[str, Any],
    *,
    direction: str,
    acceptance: float,
    minimum_imbalance: float,
    minimum_flow_z: float,
    minimum_body_atr: float,
) -> bool:
    atr = row.get("atr")
    if atr is None or float(atr) <= 0:
        return False
    open_price = float(row["open"])
    close = float(row["close"])
    outside = close > acceptance if direction == "LONG" else close < acceptance
    same_flow = (
        float(row["imbalance"]) >= minimum_imbalance
        if direction == "LONG"
        else float(row["imbalance"]) <= -minimum_imbalance
    )
    directional_body = close > open_price if direction == "LONG" else close < open_price
    displacement = abs(close - open_price) >= minimum_body_atr * float(atr)
    return (
        outside
        and same_flow
        and float(row["flow_z"]) >= minimum_flow_z
        and directional_body
        and displacement
    )


def hold_is_accepted(
    row: dict[str, Any],
    *,
    direction: str,
    pool: float,
    acceptance: float,
) -> bool:
    if direction == "LONG":
        return float(row["low"]) > pool and float(row["close"]) > acceptance
    return float(row["high"]) < pool and float(row["close"]) < acceptance


def classify(scenario: dict[str, Any], flow_logic: dict[str, Any]) -> dict[str, Any]:
    direction = str(scenario["continuation_direction"])
    pool = float(scenario["liquidity_level"])
    acceptance = float(scenario["acceptance_level"])
    bars = list(scenario.get("path_bars") or [])
    result: dict[str, Any] = {
        "scenario_id": scenario["scenario_id"],
        "source_status": scenario["status"],
        "source_direction": scenario["source_direction"],
        "continuation_direction": direction,
        "liquidity_level": pool,
        "acceptance_level": acceptance,
        "outcome": "NO_DIRECT_ACCEPTANCE_IN_RECORDED_PATH",
        "trigger": None,
        "hold": None,
        "targets": {},
    }
    for index, row in enumerate(bars):
        if is_pool_reclaimed(row, direction=direction, pool=pool):
            result.update(
                {
                    "outcome": "POOL_RECLAIMED_BEFORE_CONFIRMATION",
                    "terminal_bar": row,
                }
            )
            return result
        if not is_trigger(
            row,
            direction=direction,
            acceptance=acceptance,
            minimum_imbalance=float(flow_logic["absorption_min_imbalance"]),
            minimum_flow_z=float(flow_logic["absorption_flow_z"]),
            minimum_body_atr=float(flow_logic["confirmation_body_atr"]),
        ):
            continue
        result["trigger"] = row
        if index + 1 >= len(bars):
            result["outcome"] = "TRIGGER_WITHOUT_RECORDED_HOLD"
            return result
        hold = bars[index + 1]
        result["hold"] = hold
        if hold_is_accepted(
            hold,
            direction=direction,
            pool=pool,
            acceptance=acceptance,
        ):
            result["outcome"] = "DIRECT_ACCEPTANCE_CONFIRMED"
            minimum_rr = float(flow_logic["minimum_rr"])
            for label, target in (scenario.get("targets") or {}).items():
                record = dict(target)
                if (
                    label not in {"1.0R", "1.5R", "2.0R", "3.0R"}
                    and float(record["rr"]) < minimum_rr
                ):
                    record["outcome"] = "BELOW_MINIMUM_RR"
                    record["timestamp_ns"] = None
                result["targets"][label] = record
            return result
        result.update(
            {
                "outcome": (
                    "TRIGGER_FAILED_AND_POOL_RECLAIMED"
                    if is_pool_reclaimed(hold, direction=direction, pool=pool)
                    else "TRIGGER_FAILED_INTRABAR_POOL_HOLD"
                ),
                "terminal_bar": hold,
            }
        )
        return result
    return result


def run(args: argparse.Namespace) -> int:
    config = read_json(args.config)
    stage_dir = args.output.resolve() / args.stage
    raw = read_json(stage_dir / "failed_flow_diagnostic.json")
    flow_logic = dict(config["flow_logic"])
    scenarios = [classify(dict(item), flow_logic) for item in raw["scenarios"]]
    outcomes = Counter(str(item["outcome"]) for item in scenarios)
    target_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for item in scenarios:
        if item["outcome"] != "DIRECT_ACCEPTANCE_CONFIRMED":
            continue
        for label, target in item["targets"].items():
            target_counts[label][str(target["outcome"])] += 1
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "purpose": "strict causal post-processing; no orders or hypothetical NAV",
        "rules": [
            "a completed close through the original pool terminates the episode",
            "acceptance trigger requires structural-boundary close, original-direction aggressor imbalance, flow impulse, and displacement",
            "the next completed signal bar must remain entirely outside the original pool and close beyond the boundary",
            "a re-break after termination is a new auction and is not credited to the failed absorption",
        ],
        "outcome_counts": dict(sorted(outcomes.items())),
        "target_outcome_counts": {
            label: dict(sorted(counts.items()))
            for label, counts in sorted(target_counts.items())
        },
        "scenarios": scenarios,
    }
    write_json_atomic(stage_dir / "failed_flow_strict.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    result.add_argument("--stage", default="week-1")
    result.add_argument("--output", type=Path, required=True)
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
