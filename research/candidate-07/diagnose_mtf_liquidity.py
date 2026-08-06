#!/usr/bin/env python3
"""Diagnose 15-minute confirmed swing liquidity with one-minute flow execution.

Liquidity formation and execution timing are deliberately separated:

- 15-minute bars define confirmed swing-high and swing-low pools. A pivot is
  known only after two completed bars on its right.
- One-minute bars consume a pool on first causal contact.
- Low-efficiency failed aggression routes to reversal confirmation.
- High-efficiency accepted aggression routes only after the next completed
  minute holds outside while counterflow mitigates the displacement.
- Stops stay beyond the observed sweep or broken pool. Targets are the next
  unconsumed confirmed swing pool or a measured displacement extension.

The diagnostic enforces the global one-slot rule and emits market-path evidence
only. It creates no orders, fills, cash ledger, PnL, or hypothetical NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any

import pandas as pd

from data_flow import load_flow_bundle
from diagnose_failed_flow import read_json, target_outcome
from smc_ict_4.manifest import write_json_atomic


@dataclass(slots=True)
class Pool:
    pool_id: str
    side: str
    level: float
    pivot_ts_ns: int
    confirmed_ts_ns: int
    consumed: bool = False
    consumed_ts_ns: int | None = None


@dataclass(slots=True)
class Episode:
    scenario_id: str
    kind: str
    direction: str
    contact_index: int
    pool_id: str
    liquidity_level: float
    extreme: float
    atr: float
    contact_range: float
    contact_close: float


def minute_features(frame: pd.DataFrame, flow_period: int, atr_period: int) -> pd.DataFrame:
    bars = frame.copy()
    bars["timestamp_ns"] = bars.index.map(lambda value: int(value.value))
    bars["timestamp"] = bars.index
    bars["delta"] = 2.0 * bars["taker_buy_base"] - bars["volume"]
    bars["imbalance"] = (
        bars["delta"] / bars["volume"].where(bars["volume"] > 0)
    ).fillna(0.0)
    prior_abs_delta = bars["delta"].abs().shift(1)
    mean = prior_abs_delta.rolling(flow_period, min_periods=flow_period).mean()
    std = prior_abs_delta.rolling(flow_period, min_periods=flow_period).std(ddof=0)
    bars["flow_z"] = (
        (bars["delta"].abs() - mean) / std.where(std > 1e-12)
    ).fillna(0.0)
    previous_close = bars["close"].shift(1)
    true_range = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous_close).abs(),
            (bars["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["atr"] = true_range.shift(1).rolling(
        atr_period,
        min_periods=atr_period,
    ).mean()
    return bars.reset_index(drop=True)


def context_bars(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["bucket"] = work.index.floor("15min")
    grouped = work.groupby("bucket", sort=True)
    context = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    context["timestamp_ns"] = grouped.apply(
        lambda part: int(part.index[-1].value),
        include_groups=False,
    )
    context["timestamp"] = pd.to_datetime(
        context["timestamp_ns"],
        unit="ns",
        utc=True,
    )
    return context.reset_index(drop=True)


def pool_confirmations(context: pd.DataFrame) -> dict[int, list[Pool]]:
    events: dict[int, list[Pool]] = defaultdict(list)
    for center in range(2, len(context) - 2):
        row = context.iloc[center]
        left = context.iloc[center - 2 : center]
        right = context.iloc[center + 1 : center + 3]
        confirmation_ts = int(context.iloc[center + 2]["timestamp_ns"])
        high = float(row["high"])
        low = float(row["low"])
        if high > float(left["high"].max()) and high > float(right["high"].max()):
            pivot_ts = int(row["timestamp_ns"])
            events[confirmation_ts].append(
                Pool(
                    pool_id=f"15H-{pivot_ts}",
                    side="UPPER",
                    level=high,
                    pivot_ts_ns=pivot_ts,
                    confirmed_ts_ns=confirmation_ts,
                )
            )
        if low < float(left["low"].min()) and low < float(right["low"].min()):
            pivot_ts = int(row["timestamp_ns"])
            events[confirmation_ts].append(
                Pool(
                    pool_id=f"15L-{pivot_ts}",
                    side="LOWER",
                    level=low,
                    pivot_ts_ns=pivot_ts,
                    confirmed_ts_ns=confirmation_ts,
                )
            )
    return events


def past_efficiency(bars: pd.DataFrame, index: int, period: int) -> tuple[float, float]:
    if index < period:
        return 0.0, 0.0
    closes = [float(value) for value in bars.iloc[index - period : index]["close"]]
    path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    displacement = closes[-1] - closes[0]
    efficiency = abs(displacement) / path if path > 0 else 0.0
    slope = displacement / max(1, len(closes) - 1)
    return efficiency, slope


def bar_payload(row: pd.Series) -> dict[str, Any]:
    return {
        "timestamp_ns": int(row["timestamp_ns"]),
        "timestamp": row["timestamp"].isoformat(),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"]),
        "imbalance": float(row["imbalance"]),
        "flow_z": float(row["flow_z"]),
        "atr": None if pd.isna(row["atr"]) else float(row["atr"]),
    }


def touched_pools(
    pools: dict[str, Pool],
    row: pd.Series,
    previous_close: float,
) -> tuple[list[Pool], list[Pool]]:
    upper = [
        pool
        for pool in pools.values()
        if not pool.consumed
        and pool.side == "UPPER"
        and pool.level >= previous_close
        and float(row["high"]) >= pool.level
    ]
    lower = [
        pool
        for pool in pools.values()
        if not pool.consumed
        and pool.side == "LOWER"
        and pool.level <= previous_close
        and float(row["low"]) <= pool.level
    ]
    upper.sort(key=lambda item: item.level)
    lower.sort(key=lambda item: item.level, reverse=True)
    return upper, lower


def structural_targets(
    pools: dict[str, Pool],
    *,
    direction: str,
    entry: float,
    stop: float,
    contact_range: float,
) -> dict[str, float]:
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0:
        return {}
    targets: dict[str, float] = {
        "1.0R": entry + risk if direction == "LONG" else entry - risk,
        "1.5R": entry + 1.5 * risk if direction == "LONG" else entry - 1.5 * risk,
        "2.0R": entry + 2.0 * risk if direction == "LONG" else entry - 2.0 * risk,
    }
    side = "UPPER" if direction == "LONG" else "LOWER"
    candidates = sorted(
        (
            pool.level
            for pool in pools.values()
            if not pool.consumed
            and pool.side == side
            and (pool.level > entry if direction == "LONG" else pool.level < entry)
        ),
        reverse=direction == "SHORT",
    )
    for number, level in enumerate(candidates[:3], start=1):
        targets[f"CONFIRMED_SWING_POOL_{number}"] = float(level)
    measured = (
        entry + contact_range
        if direction == "LONG"
        else entry - contact_range
    )
    if measured > entry if direction == "LONG" else measured < entry:
        targets["CONTACT_RANGE_EXTENSION"] = measured
    return targets


def evaluate_route(
    bars: pd.DataFrame,
    pools: dict[str, Pool],
    *,
    episode: Episode,
    confirmation_index: int,
    flow_logic: dict[str, Any],
    max_hold_minutes: int,
) -> tuple[dict[str, Any], int]:
    confirmation = bars.iloc[confirmation_index]
    entry = float(confirmation["close"])
    buffer = float(flow_logic["stop_buffer_atr"]) * episode.atr
    if episode.direction == "LONG":
        stop = min(episode.extreme, episode.liquidity_level) - buffer
        risk = entry - stop
    else:
        stop = max(episode.extreme, episode.liquidity_level) + buffer
        risk = stop - entry
    record: dict[str, Any] = {
        "scenario_id": episode.scenario_id,
        "kind": episode.kind,
        "direction": episode.direction,
        "pool_id": episode.pool_id,
        "liquidity_level": episode.liquidity_level,
        "contact": bar_payload(bars.iloc[episode.contact_index]),
        "confirmation": bar_payload(confirmation),
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "risk_atr": risk / episode.atr if episode.atr > 0 else None,
    }
    if risk <= 0:
        record["outcome"] = "NONPOSITIVE_RISK"
        return record, confirmation_index
    levels = structural_targets(
        pools,
        direction=episode.direction,
        entry=entry,
        stop=stop,
        contact_range=episode.contact_range,
    )
    future = bars.iloc[
        confirmation_index + 1 : confirmation_index + 1 + max_hold_minutes
    ]
    minimum_rr = float(flow_logic["minimum_rr"])
    record["targets"] = {}
    for label, target in levels.items():
        rr = abs(target - entry) / risk
        target_record: dict[str, Any] = {"price": target, "rr": rr}
        if (
            label not in {"1.0R", "1.5R", "2.0R"}
            and rr < minimum_rr
        ):
            target_record.update(
                {"outcome": "BELOW_MINIMUM_RR", "timestamp_ns": None}
            )
        else:
            target_record.update(
                target_outcome(future, episode.direction, stop, target)
            )
        record["targets"][label] = target_record

    tradeable = [
        (label, value)
        for label, value in record["targets"].items()
        if label.startswith("CONFIRMED_SWING_POOL_")
        and float(value["rr"]) >= minimum_rr
    ]
    if tradeable:
        selected_label, selected = tradeable[0]
    else:
        measured = record["targets"].get("CONTACT_RANGE_EXTENSION")
        if measured is not None and float(measured["rr"]) >= minimum_rr:
            selected_label, selected = "CONTACT_RANGE_EXTENSION", measured
        else:
            selected_label, selected = None, None
    record["selected_target"] = selected_label
    record["outcome"] = (
        str(selected["outcome"])
        if selected is not None
        else "NO_STRUCTURAL_TARGET_AT_MINIMUM_RR"
    )
    if selected is not None and selected.get("timestamp_ns") is not None:
        matches = bars.index[
            bars["timestamp_ns"] == int(selected["timestamp_ns"])
        ].tolist()
        block_until = int(matches[0]) if matches else confirmation_index
    else:
        block_until = (
            min(len(bars) - 1, confirmation_index + max_hold_minutes)
            if selected is not None
            else confirmation_index
        )
    return record, block_until


def diagnose(
    bars: pd.DataFrame,
    *,
    confirmations: dict[int, list[Pool]],
    flow_logic: dict[str, Any],
    trade_start_ns: int,
    trade_end_ns: int,
    max_hold_minutes: int,
) -> dict[str, Any]:
    pools: dict[str, Pool] = {}
    episode: Episode | None = None
    scenarios: list[dict[str, Any]] = []
    contact_counts: Counter[str] = Counter()
    block_until = -1
    index = 1

    while index < len(bars):
        row = bars.iloc[index]
        timestamp_ns = int(row["timestamp_ns"])
        for pool in confirmations.get(timestamp_ns, []):
            pools[pool.pool_id] = pool

        previous_close = float(bars.iloc[index - 1]["close"])
        upper, lower = touched_pools(pools, row, previous_close)

        if index <= block_until:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            index += 1
            continue

        if episode is not None:
            age = index - episode.contact_index
            if age > int(flow_logic["confirmation_bars"]):
                scenarios.append(
                    {
                        "scenario_id": episode.scenario_id,
                        "kind": episode.kind,
                        "direction": episode.direction,
                        "pool_id": episode.pool_id,
                        "outcome": "CONFIRMATION_TIMEOUT",
                        "contact": bar_payload(bars.iloc[episode.contact_index]),
                    }
                )
                episode = None
            else:
                atr = float(row["atr"]) if not pd.isna(row["atr"]) else episode.atr
                body_ok = abs(float(row["close"]) - float(row["open"])) >= (
                    float(flow_logic["confirmation_body_atr"]) * atr
                )
                if episode.kind == "SWING_POOL_REVERSAL":
                    midpoint = 0.5 * (
                        episode.extreme + episode.liquidity_level
                    )
                    if episode.direction == "SHORT":
                        confirmed = (
                            body_ok
                            and float(row["close"]) < midpoint
                            and float(row["close"]) < float(row["open"])
                            and float(row["imbalance"])
                            <= -float(flow_logic["confirmation_min_imbalance"])
                        )
                        invalid = float(row["close"]) > episode.extreme
                    else:
                        confirmed = (
                            body_ok
                            and float(row["close"]) > midpoint
                            and float(row["close"]) > float(row["open"])
                            and float(row["imbalance"])
                            >= float(flow_logic["confirmation_min_imbalance"])
                        )
                        invalid = float(row["close"]) < episode.extreme
                else:
                    tolerance = float(flow_logic["reclaim_buffer_atr"]) * episode.atr
                    if episode.direction == "LONG":
                        outside = (
                            float(row["low"]) >= episode.liquidity_level - tolerance
                            and float(row["close"]) > episode.liquidity_level
                        )
                        mitigated = (
                            float(row["close"]) < float(row["open"])
                            and float(row["imbalance"])
                            <= -float(flow_logic["confirmation_min_imbalance"])
                        )
                    else:
                        outside = (
                            float(row["high"]) <= episode.liquidity_level + tolerance
                            and float(row["close"]) < episode.liquidity_level
                        )
                        mitigated = (
                            float(row["close"]) > float(row["open"])
                            and float(row["imbalance"])
                            >= float(flow_logic["confirmation_min_imbalance"])
                        )
                    confirmed = outside and mitigated
                    invalid = not outside

                if confirmed and trade_start_ns <= timestamp_ns < trade_end_ns:
                    record, block_until = evaluate_route(
                        bars,
                        pools,
                        episode=episode,
                        confirmation_index=index,
                        flow_logic=flow_logic,
                        max_hold_minutes=max_hold_minutes,
                    )
                    scenarios.append(record)
                    episode = None
                    index += 1
                    continue
                if invalid:
                    scenarios.append(
                        {
                            "scenario_id": episode.scenario_id,
                            "kind": episode.kind,
                            "direction": episode.direction,
                            "pool_id": episode.pool_id,
                            "outcome": "SCENARIO_INVALIDATED",
                            "contact": bar_payload(
                                bars.iloc[episode.contact_index]
                            ),
                            "terminal": bar_payload(row),
                        }
                    )
                    episode = None

        if upper and lower:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            contact_counts["AMBIGUOUS_BOTH_SIDES"] += 1
            index += 1
            continue
        contacts = upper or lower
        if not contacts:
            index += 1
            continue
        pool = contacts[0]
        for crossed in contacts:
            crossed.consumed = True
            crossed.consumed_ts_ns = timestamp_ns

        atr_value = row["atr"]
        if pd.isna(atr_value) or float(atr_value) <= 0:
            contact_counts["NO_ATR"] += 1
            index += 1
            continue
        atr = float(atr_value)
        efficiency, slope = past_efficiency(
            bars,
            index,
            int(flow_logic["atr_period"]),
        )
        bar_range = max(
            1e-12,
            float(row["high"]) - float(row["low"]),
        )
        if pool.side == "UPPER":
            penetration = (float(row["high"]) - pool.level) / atr
            wick = (
                float(row["high"])
                - max(float(row["open"]), float(row["close"]))
            ) / bar_range
            reversal = (
                efficiency <= float(flow_logic["reversal_efficiency_max"])
                and float(flow_logic["sweep_min_atr"])
                <= penetration
                <= float(flow_logic["sweep_max_atr"])
                and float(row["close"])
                < pool.level - float(flow_logic["reclaim_buffer_atr"]) * atr
                and wick >= float(flow_logic["sweep_wick_fraction"])
                and float(row["imbalance"])
                >= float(flow_logic["absorption_min_imbalance"])
                and float(row["flow_z"])
                >= float(flow_logic["absorption_flow_z"])
            )
            acceptance = (
                efficiency >= float(flow_logic["reversal_efficiency_max"])
                and slope > 0
                and float(row["close"])
                >= pool.level + float(flow_logic["sweep_min_atr"]) * atr
                and float(row["close"]) > float(row["open"])
                and abs(float(row["close"]) - float(row["open"]))
                >= float(flow_logic["confirmation_body_atr"]) * atr
                and float(row["imbalance"])
                >= float(flow_logic["absorption_min_imbalance"])
                and float(row["flow_z"])
                >= float(flow_logic["absorption_flow_z"])
            )
            reversal_direction = "SHORT"
            acceptance_direction = "LONG"
            extreme = float(row["high"])
        else:
            penetration = (pool.level - float(row["low"])) / atr
            wick = (
                min(float(row["open"]), float(row["close"]))
                - float(row["low"])
            ) / bar_range
            reversal = (
                efficiency <= float(flow_logic["reversal_efficiency_max"])
                and float(flow_logic["sweep_min_atr"])
                <= penetration
                <= float(flow_logic["sweep_max_atr"])
                and float(row["close"])
                > pool.level + float(flow_logic["reclaim_buffer_atr"]) * atr
                and wick >= float(flow_logic["sweep_wick_fraction"])
                and float(row["imbalance"])
                <= -float(flow_logic["absorption_min_imbalance"])
                and float(row["flow_z"])
                >= float(flow_logic["absorption_flow_z"])
            )
            acceptance = (
                efficiency >= float(flow_logic["reversal_efficiency_max"])
                and slope < 0
                and float(row["close"])
                <= pool.level - float(flow_logic["sweep_min_atr"]) * atr
                and float(row["close"]) < float(row["open"])
                and abs(float(row["close"]) - float(row["open"]))
                >= float(flow_logic["confirmation_body_atr"]) * atr
                and float(row["imbalance"])
                <= -float(flow_logic["absorption_min_imbalance"])
                and float(row["flow_z"])
                >= float(flow_logic["absorption_flow_z"])
            )
            reversal_direction = "LONG"
            acceptance_direction = "SHORT"
            extreme = float(row["low"])

        if reversal:
            contact_counts["SWING_POOL_REVERSAL"] += 1
            episode = Episode(
                scenario_id=f"c07m1-{timestamp_ns}-{pool.pool_id}-r",
                kind="SWING_POOL_REVERSAL",
                direction=reversal_direction,
                contact_index=index,
                pool_id=pool.pool_id,
                liquidity_level=pool.level,
                extreme=extreme,
                atr=atr,
                contact_range=bar_range,
                contact_close=float(row["close"]),
            )
        elif acceptance:
            contact_counts["SWING_POOL_ACCEPTANCE"] += 1
            episode = Episode(
                scenario_id=f"c07m1-{timestamp_ns}-{pool.pool_id}-a",
                kind="SWING_POOL_ACCEPTANCE",
                direction=acceptance_direction,
                contact_index=index,
                pool_id=pool.pool_id,
                liquidity_level=pool.level,
                extreme=extreme,
                atr=atr,
                contact_range=bar_range,
                contact_close=float(row["close"]),
            )
        else:
            contact_counts["UNROUTED_FIRST_CONTACT"] += 1
        index += 1

    outcome_counts = Counter(str(item["outcome"]) for item in scenarios)
    by_kind: dict[str, Counter[str]] = defaultdict(Counter)
    selected_counts: dict[str, Counter[str]] = defaultdict(Counter)
    target_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for item in scenarios:
        by_kind[str(item.get("kind", "UNKNOWN"))][str(item["outcome"])] += 1
        selected = item.get("selected_target")
        if selected is not None:
            selected_counts[str(selected)][str(item["outcome"])] += 1
        for label, target in (item.get("targets") or {}).items():
            target_counts[label][str(target["outcome"])] += 1

    return {
        "pool_confirmations": sum(len(items) for items in confirmations.values()),
        "pools_active_or_consumed": len(pools),
        "contact_counts": dict(sorted(contact_counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "by_kind_outcomes": {
            kind: dict(sorted(counts.items()))
            for kind, counts in sorted(by_kind.items())
        },
        "selected_target_outcomes": {
            label: dict(sorted(counts.items()))
            for label, counts in sorted(selected_counts.items())
        },
        "target_outcome_counts": {
            label: dict(sorted(counts.items()))
            for label, counts in sorted(target_counts.items())
        },
        "scenarios": scenarios,
    }


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
        manifest_destination=stage_dir / "mtf_liquidity_data_manifest.json",
    )
    flow_logic = dict(config["flow_logic"])
    bars = minute_features(
        bundle.frame,
        int(flow_logic["flow_period"]),
        int(flow_logic["atr_period"]),
    )
    context = context_bars(bundle.frame)
    confirmations = pool_confirmations(context)
    start_ns = int(pd.Timestamp(start, tz="UTC").value)
    end_ns = int(pd.Timestamp(end, tz="UTC").value)
    result = diagnose(
        bars,
        confirmations=confirmations,
        flow_logic=flow_logic,
        trade_start_ns=start_ns,
        trade_end_ns=end_ns,
        max_hold_minutes=int(config["max_hold_minutes"]),
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "purpose": (
            "15-minute swing-liquidity / one-minute execution diagnostic; "
            "no orders or hypothetical NAV"
        ),
        "logic": [
            "a 15-minute swing pool is known only after two completed right-side bars",
            "the pool is consumed on first one-minute contact",
            "low-efficiency failed aggression and high-efficiency accepted aggression are separate routes",
            "one-minute confirmation supplies timing while the 15-minute inventory supplies structural targets",
            "the global one-slot rule blocks overlapping diagnostic routes",
        ],
        **result,
    }
    write_json_atomic(stage_dir / "mtf_liquidity_diagnostic.json", payload)
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
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
