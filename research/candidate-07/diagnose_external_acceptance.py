#!/usr/bin/env python3
"""Diagnose flow-confirmed acceptance of previously formed external liquidity.

This script tests a scenario which is distinct from absorption reversal: a
pre-existing external pool is penetrated, price closes beyond it with matching
aggressor flow during an already directional auction, and the next completed
signal bar holds outside. It produces market-path evidence only; it does not
simulate orders, fills, PnL, or NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import json
from pathlib import Path
from typing import Any

import pandas as pd

from data_flow import load_flow_bundle
from diagnose_failed_flow import aggregate_flow, read_json, target_outcome
from smc_ict_4.manifest import write_json_atomic


def past_trend_state(bars: pd.DataFrame, period: int) -> pd.DataFrame:
    previous = bars["close"].shift(1)
    displacement = previous - bars["close"].shift(period)
    path = bars["close"].diff().abs().shift(1).rolling(
        period - 1,
        min_periods=period - 1,
    ).sum()
    result = pd.DataFrame(index=bars.index)
    result["trend_efficiency"] = (
        displacement.abs() / path.where(path > 0)
    ).fillna(0.0)
    result["trend_slope"] = (displacement / max(1, period - 1)).fillna(0.0)
    return result


def rolling_level(
    bars: pd.DataFrame,
    index: int,
    lookback: int,
    side: str,
) -> tuple[float, int]:
    window = bars.iloc[index - lookback : index]
    if side == "UPPER":
        position = int(window["high"].to_numpy().argmax())
        row = window.iloc[position]
        return float(row["high"]), int(row["timestamp_ns"])
    position = int(window["low"].to_numpy().argmin())
    row = window.iloc[position]
    return float(row["low"]), int(row["timestamp_ns"])


def target_candidates(
    bars: pd.DataFrame,
    index: int,
    *,
    direction: str,
    entry: float,
    risk: float,
    lower: float,
    upper: float,
) -> dict[str, float]:
    targets: dict[str, float] = {
        f"{rr:.1f}R": (
            entry + risk * rr if direction == "LONG" else entry - risk * rr
        )
        for rr in (1.0, 1.5, 2.0, 3.0)
    }
    history = bars.iloc[:index]
    for label, count in (
        ("PRIOR_12H", 144),
        ("PRIOR_24H", 288),
        ("PRIOR_72H", 864),
    ):
        window = history.tail(count)
        level = (
            float(window["high"].max())
            if direction == "LONG"
            else float(window["low"].min())
        )
        favorable = level > entry if direction == "LONG" else level < entry
        if favorable:
            targets[label] = level
    width = upper - lower
    broken = upper if direction == "LONG" else lower
    for fraction in (0.25, 0.50, 1.00):
        level = (
            broken + width * fraction
            if direction == "LONG"
            else broken - width * fraction
        )
        favorable = level > entry if direction == "LONG" else level < entry
        if favorable:
            targets[f"DEALING_RANGE_EXTENSION_{fraction:.2f}"] = level
    return targets


def diagnose(
    bars: pd.DataFrame,
    flow_logic: dict[str, Any],
    trade_start_ns: int,
    trade_end_ns: int,
    lookahead_bars: int,
) -> list[dict[str, Any]]:
    trend = past_trend_state(bars, int(flow_logic["atr_period"]))
    work = pd.concat([bars, trend], axis=1)
    external = int(flow_logic["external_lookback"])
    consumed: set[tuple[str, int]] = set()
    results: list[dict[str, Any]] = []
    first_index = max(int(flow_logic["min_history"]), external)
    for index in range(first_index, len(work) - 1):
        row = work.iloc[index]
        timestamp_ns = int(row["timestamp_ns"])
        if not trade_start_ns <= timestamp_ns < trade_end_ns:
            continue
        atr = float(row["atr"])
        if not atr > 0:
            continue
        upper, upper_ns = rolling_level(work, index, external, "UPPER")
        lower, lower_ns = rolling_level(work, index, external, "LOWER")
        contact_distance = float(flow_logic["sweep_min_atr"]) * atr
        upper_contact = float(row["high"]) >= upper + contact_distance
        lower_contact = float(row["low"]) <= lower - contact_distance
        if upper_contact and lower_contact:
            consumed.add(("UPPER", upper_ns))
            consumed.add(("LOWER", lower_ns))
            continue
        if not upper_contact and not lower_contact:
            continue

        side = "UPPER" if upper_contact else "LOWER"
        formed_ns = upper_ns if upper_contact else lower_ns
        key = (side, formed_ns)
        if key in consumed:
            continue
        consumed.add(key)
        direction = "LONG" if side == "UPPER" else "SHORT"
        level = upper if direction == "LONG" else lower
        close = float(row["close"])
        open_price = float(row["open"])
        accepted_close = (
            close >= level + contact_distance
            if direction == "LONG"
            else close <= level - contact_distance
        )
        matching_flow = (
            float(row["imbalance"])
            >= float(flow_logic["absorption_min_imbalance"])
            if direction == "LONG"
            else float(row["imbalance"])
            <= -float(flow_logic["absorption_min_imbalance"])
        )
        directional_body = (
            close > open_price if direction == "LONG" else close < open_price
        )
        displacement = (
            abs(close - open_price)
            >= float(flow_logic["confirmation_body_atr"]) * atr
        )
        directional_regime = (
            float(row["trend_efficiency"])
            >= float(flow_logic["reversal_efficiency_max"])
            and (
                float(row["trend_slope"]) > 0
                if direction == "LONG"
                else float(row["trend_slope"]) < 0
            )
        )
        if not (
            accepted_close
            and matching_flow
            and float(row["flow_z"])
            >= float(flow_logic["absorption_flow_z"])
            and directional_body
            and displacement
            and directional_regime
        ):
            continue

        hold = work.iloc[index + 1]
        tolerance = float(flow_logic["reclaim_buffer_atr"]) * atr
        held = (
            float(hold["low"]) >= level - tolerance
            and float(hold["close"]) > level
            if direction == "LONG"
            else float(hold["high"]) <= level + tolerance
            and float(hold["close"]) < level
        )
        record: dict[str, Any] = {
            "scenario_id": f"c07a-{timestamp_ns}-{side.lower()}",
            "direction": direction,
            "liquidity_side": side,
            "liquidity_level": level,
            "liquidity_formed_ns": formed_ns,
            "lower_range": lower,
            "upper_range": upper,
            "contact": {
                "timestamp_ns": timestamp_ns,
                "timestamp": row["timestamp"].isoformat(),
                "open": open_price,
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": close,
                "atr": atr,
                "imbalance": float(row["imbalance"]),
                "flow_z": float(row["flow_z"]),
                "trend_efficiency": float(row["trend_efficiency"]),
                "trend_slope": float(row["trend_slope"]),
            },
            "hold": {
                "timestamp_ns": int(hold["timestamp_ns"]),
                "timestamp": hold["timestamp"].isoformat(),
                "open": float(hold["open"]),
                "high": float(hold["high"]),
                "low": float(hold["low"]),
                "close": float(hold["close"]),
                "imbalance": float(hold["imbalance"]),
                "flow_z": float(hold["flow_z"]),
            },
            "outcome": (
                "OUTSIDE_ACCEPTANCE_HELD"
                if held
                else "ACCEPTANCE_RECLAIMED_NEXT_BAR"
            ),
        }
        if not held:
            results.append(record)
            continue

        entry = float(hold["close"])
        stop = (
            level - float(flow_logic["stop_buffer_atr"]) * atr
            if direction == "LONG"
            else level + float(flow_logic["stop_buffer_atr"]) * atr
        )
        risk = entry - stop if direction == "LONG" else stop - entry
        record["entry"] = entry
        record["stop"] = stop
        record["risk"] = risk
        record["risk_atr"] = risk / atr if atr > 0 else None
        if risk <= 0:
            record["outcome"] = "NONPOSITIVE_RISK_AFTER_HOLD"
            results.append(record)
            continue
        future = work.iloc[index + 2 : index + 2 + lookahead_bars]
        candidates = target_candidates(
            work,
            index + 1,
            direction=direction,
            entry=entry,
            risk=risk,
            lower=lower,
            upper=upper,
        )
        minimum_rr = float(flow_logic["minimum_rr"])
        record["targets"] = {}
        for label, target in candidates.items():
            rr = abs(target - entry) / risk
            target_record: dict[str, Any] = {"price": target, "rr": rr}
            if (
                label not in {"1.0R", "1.5R", "2.0R", "3.0R"}
                and rr < minimum_rr
            ):
                target_record.update(
                    {"outcome": "BELOW_MINIMUM_RR", "timestamp_ns": None}
                )
            else:
                target_record.update(
                    target_outcome(future, direction, stop, target)
                )
            record["targets"][label] = target_record
        results.append(record)
    return results


def run(args: argparse.Namespace) -> int:
    config = read_json(args.config)
    plan = read_json(args.week_plan)
    week = next(item for item in plan["weeks"] if item["stage"] == args.stage)
    start = date.fromisoformat(str(week["start"]))
    end = date.fromisoformat(str(week["end"]))
    stage_dir = args.output.resolve() / args.stage
    bundle = load_flow_bundle(
        symbol=str(config["symbol"]),
        trade_start=start,
        trade_end=end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=stage_dir / "external_acceptance_data_manifest.json",
    )
    flow_logic = dict(config["flow_logic"])
    bars = aggregate_flow(
        bundle.frame,
        int(flow_logic["signal_minutes"]),
        int(flow_logic["flow_period"]),
    )
    start_ns = int(pd.Timestamp(start, tz="UTC").value)
    end_ns = int(pd.Timestamp(end, tz="UTC").value)
    scenarios = diagnose(
        bars,
        flow_logic,
        start_ns,
        end_ns,
        args.lookahead_bars,
    )
    outcomes = Counter(str(item["outcome"]) for item in scenarios)
    target_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for item in scenarios:
        for label, target in (item.get("targets") or {}).items():
            target_counts[label][str(target["outcome"])] += 1
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "purpose": (
            "external-liquidity acceptance diagnostic; "
            "no orders or hypothetical NAV"
        ),
        "logic": [
            "external liquidity existed before the contact bar",
            "the first causal contact closes beyond the pool with matching aggressor flow and displacement",
            "the prior auction path is directional rather than the low-efficiency absorption state",
            "the next completed signal bar holds beyond the broken pool",
        ],
        "scenarios": scenarios,
        "outcome_counts": dict(sorted(outcomes.items())),
        "target_outcome_counts": {
            label: dict(sorted(counts.items()))
            for label, counts in sorted(target_counts.items())
        },
    }
    write_json_atomic(stage_dir / "external_acceptance_diagnostic.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    result.add_argument(
        "--week-plan",
        type=Path,
        default=candidate_dir / "week_plan.json",
    )
    result.add_argument("--stage", default="week-1")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument(
        "--data-root",
        type=Path,
        default=Path(".research-data/candidate-07"),
    )
    result.add_argument("--lookahead-bars", type=int, default=24)
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
