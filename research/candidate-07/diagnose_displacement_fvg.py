#!/usr/bin/env python3
"""Diagnose displacement -> fair-value-gap mitigation -> continuation.

A completed five-minute displacement bar with matching aggressor flow and a
three-bar non-overlap creates a causal imbalance zone. The state machine then
requires:

1. a later completed signal bar to trade back into the zone with counterflow,
   without closing through the far edge;
2. the next completed signal bar to displace back in the original direction
   beyond the mitigation bar;
3. structural invalidation beyond the mitigation/FVG far edge and targets that
   were known when the route became entry-ready.

This is market-path diagnosis only. It creates no orders, fills, cash ledger,
PnL, or hypothetical NAV.
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
from diagnose_failed_flow import aggregate_flow, read_json, target_outcome
from smc_ict_4.manifest import write_json_atomic


@dataclass(slots=True)
class FvgEpisode:
    scenario_id: str
    direction: str
    impulse_index: int
    zone_low: float
    zone_high: float
    impulse_low: float
    impulse_high: float
    atr: float
    mitigation_index: int | None = None


def _bar_payload(row: pd.Series) -> dict[str, Any]:
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


def _past_efficiency(bars: pd.DataFrame, index: int, period: int) -> tuple[float, float]:
    window = bars.iloc[index - period : index]
    closes = [float(value) for value in window["close"]]
    path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    displacement = closes[-1] - closes[0]
    efficiency = abs(displacement) / path if path > 0 else 0.0
    slope = displacement / max(1, len(closes) - 1)
    return efficiency, slope


def _impulse(
    bars: pd.DataFrame,
    index: int,
    flow_logic: dict[str, Any],
) -> FvgEpisode | None:
    if index < max(2, int(flow_logic["min_history"])):
        return None
    row = bars.iloc[index]
    two_back = bars.iloc[index - 2]
    atr = row["atr"]
    if pd.isna(atr) or float(atr) <= 0:
        return None
    atr = float(atr)
    body = abs(float(row["close"]) - float(row["open"]))
    body_ok = body >= float(flow_logic["confirmation_body_atr"]) * atr
    flow_z_ok = float(row["flow_z"]) >= float(flow_logic["absorption_flow_z"])
    efficiency, slope = _past_efficiency(
        bars,
        index,
        int(flow_logic["atr_period"]),
    )
    regime_ok = efficiency >= float(flow_logic["reversal_efficiency_max"])
    long_gap = float(row["low"]) > float(two_back["high"])
    short_gap = float(row["high"]) < float(two_back["low"])
    long = (
        long_gap
        and float(row["close"]) > float(row["open"])
        and float(row["imbalance"]) >= float(flow_logic["absorption_min_imbalance"])
        and slope > 0
    )
    short = (
        short_gap
        and float(row["close"]) < float(row["open"])
        and float(row["imbalance"]) <= -float(flow_logic["absorption_min_imbalance"])
        and slope < 0
    )
    if not (body_ok and flow_z_ok and regime_ok):
        return None
    if long == short:
        return None
    if long:
        zone_low = float(two_back["high"])
        zone_high = float(row["low"])
        direction = "LONG"
    else:
        zone_low = float(row["high"])
        zone_high = float(two_back["low"])
        direction = "SHORT"
    return FvgEpisode(
        scenario_id=f"c07fvg-{int(row['timestamp_ns'])}-{direction.lower()}",
        direction=direction,
        impulse_index=index,
        zone_low=zone_low,
        zone_high=zone_high,
        impulse_low=min(
            float(bars.iloc[index - 2]["low"]),
            float(bars.iloc[index - 1]["low"]),
            float(row["low"]),
        ),
        impulse_high=max(
            float(bars.iloc[index - 2]["high"]),
            float(bars.iloc[index - 1]["high"]),
            float(row["high"]),
        ),
        atr=atr,
    )


def _touches_zone(row: pd.Series, episode: FvgEpisode) -> bool:
    return (
        float(row["low"]) <= episode.zone_high
        and float(row["high"]) >= episode.zone_low
    )


def _far_edge_reclaimed(row: pd.Series, episode: FvgEpisode) -> bool:
    return (
        float(row["close"]) < episode.zone_low
        if episode.direction == "LONG"
        else float(row["close"]) > episode.zone_high
    )


def _counterflow(row: pd.Series, episode: FvgEpisode, threshold: float) -> bool:
    return (
        float(row["imbalance"]) <= -threshold
        if episode.direction == "LONG"
        else float(row["imbalance"]) >= threshold
    )


def _continuation_confirmed(
    row: pd.Series,
    mitigation: pd.Series,
    episode: FvgEpisode,
    flow_logic: dict[str, Any],
) -> bool:
    atr = episode.atr
    body_ok = abs(float(row["close"]) - float(row["open"])) >= (
        float(flow_logic["confirmation_body_atr"]) * atr
    )
    if episode.direction == "LONG":
        return (
            body_ok
            and float(row["close"]) > float(row["open"])
            and float(row["close"]) > float(mitigation["high"])
            and float(row["imbalance"]) >= float(
                flow_logic["confirmation_min_imbalance"]
            )
        )
    return (
        body_ok
        and float(row["close"]) < float(row["open"])
        and float(row["close"]) < float(mitigation["low"])
        and float(row["imbalance"]) <= -float(
            flow_logic["confirmation_min_imbalance"]
        )
    )


def _targets(
    bars: pd.DataFrame,
    *,
    episode: FvgEpisode,
    confirmation_index: int,
    entry: float,
    stop: float,
    flow_logic: dict[str, Any],
    lookahead_bars: int,
) -> dict[str, dict[str, Any]]:
    direction = episode.direction
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0:
        return {}
    history = bars.iloc[
        max(0, episode.impulse_index - int(flow_logic["external_lookback"])) :
        episode.impulse_index
    ]
    levels: dict[str, float] = {
        "1.0R": entry + risk if direction == "LONG" else entry - risk,
        "1.5R": entry + 1.5 * risk if direction == "LONG" else entry - 1.5 * risk,
        "2.0R": entry + 2.0 * risk if direction == "LONG" else entry - 2.0 * risk,
    }
    external = (
        float(history["high"].max())
        if direction == "LONG"
        else float(history["low"].min())
    )
    favorable = external > entry if direction == "LONG" else external < entry
    if favorable:
        levels["PRE_IMPULSE_EXTERNAL_LIQUIDITY"] = external
    impulse_range = episode.impulse_high - episode.impulse_low
    measured = (
        episode.impulse_high + impulse_range
        if direction == "LONG"
        else episode.impulse_low - impulse_range
    )
    favorable = measured > entry if direction == "LONG" else measured < entry
    if favorable:
        levels["IMPULSE_RANGE_EXTENSION"] = measured

    future = bars.iloc[
        confirmation_index + 1 : confirmation_index + 1 + lookahead_bars
    ]
    result: dict[str, dict[str, Any]] = {}
    minimum_rr = float(flow_logic["minimum_rr"])
    for label, target in levels.items():
        rr = abs(target - entry) / risk
        record: dict[str, Any] = {"price": target, "rr": rr}
        if label not in {"1.0R", "1.5R", "2.0R"} and rr < minimum_rr:
            record.update({"outcome": "BELOW_MINIMUM_RR", "timestamp_ns": None})
        else:
            record.update(target_outcome(future, direction, stop, target))
        result[label] = record
    return result


def diagnose(
    bars: pd.DataFrame,
    *,
    flow_logic: dict[str, Any],
    trade_start_ns: int,
    trade_end_ns: int,
    lookahead_bars: int,
) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    episode: FvgEpisode | None = None
    counter = 0
    confirmation_horizon = int(flow_logic["confirmation_bars"])

    for index in range(len(bars)):
        row = bars.iloc[index]
        timestamp_ns = int(row["timestamp_ns"])

        if episode is None:
            candidate = _impulse(bars, index, flow_logic)
            if candidate is None:
                continue
            counter += 1
            candidate.scenario_id = (
                f"c07fvg-{timestamp_ns}-{counter:06d}"
            )
            episode = candidate
            continue

        age = index - episode.impulse_index
        if age > confirmation_horizon + 1:
            scenarios.append(
                {
                    "scenario_id": episode.scenario_id,
                    "direction": episode.direction,
                    "outcome": "FVG_MITIGATION_TIMEOUT",
                    "impulse": _bar_payload(bars.iloc[episode.impulse_index]),
                    "zone": {
                        "low": episode.zone_low,
                        "high": episode.zone_high,
                    },
                }
            )
            episode = None
            candidate = _impulse(bars, index, flow_logic)
            if candidate is not None:
                counter += 1
                candidate.scenario_id = f"c07fvg-{timestamp_ns}-{counter:06d}"
                episode = candidate
            continue

        if episode.mitigation_index is None:
            if _far_edge_reclaimed(row, episode):
                scenarios.append(
                    {
                        "scenario_id": episode.scenario_id,
                        "direction": episode.direction,
                        "outcome": "FVG_FAR_EDGE_RECLAIMED",
                        "impulse": _bar_payload(
                            bars.iloc[episode.impulse_index]
                        ),
                        "terminal": _bar_payload(row),
                        "zone": {
                            "low": episode.zone_low,
                            "high": episode.zone_high,
                        },
                    }
                )
                episode = None
                continue
            if (
                _touches_zone(row, episode)
                and _counterflow(
                    row,
                    episode,
                    float(flow_logic["confirmation_min_imbalance"]),
                )
            ):
                episode.mitigation_index = index
            continue

        mitigation = bars.iloc[episode.mitigation_index]
        if _far_edge_reclaimed(row, episode):
            scenarios.append(
                {
                    "scenario_id": episode.scenario_id,
                    "direction": episode.direction,
                    "outcome": "FVG_RECLAIMED_AFTER_MITIGATION",
                    "impulse": _bar_payload(
                        bars.iloc[episode.impulse_index]
                    ),
                    "mitigation": _bar_payload(mitigation),
                    "terminal": _bar_payload(row),
                    "zone": {
                        "low": episode.zone_low,
                        "high": episode.zone_high,
                    },
                }
            )
            episode = None
            continue
        if not _continuation_confirmed(
            row,
            mitigation,
            episode,
            flow_logic,
        ):
            scenarios.append(
                {
                    "scenario_id": episode.scenario_id,
                    "direction": episode.direction,
                    "outcome": "MITIGATION_NOT_REDISPLACED_NEXT_BAR",
                    "impulse": _bar_payload(
                        bars.iloc[episode.impulse_index]
                    ),
                    "mitigation": _bar_payload(mitigation),
                    "confirmation": _bar_payload(row),
                    "zone": {
                        "low": episode.zone_low,
                        "high": episode.zone_high,
                    },
                }
            )
            episode = None
            continue

        if not trade_start_ns <= timestamp_ns < trade_end_ns:
            episode = None
            continue
        entry = float(row["close"])
        if episode.direction == "LONG":
            stop = min(float(mitigation["low"]), episode.zone_low) - (
                float(flow_logic["stop_buffer_atr"]) * episode.atr
            )
            risk = entry - stop
        else:
            stop = max(float(mitigation["high"]), episode.zone_high) + (
                float(flow_logic["stop_buffer_atr"]) * episode.atr
            )
            risk = stop - entry
        if risk <= 0:
            outcome = "NONPOSITIVE_FVG_RISK"
            targets = {}
        else:
            outcome = "FVG_CONTINUATION_READY"
            targets = _targets(
                bars,
                episode=episode,
                confirmation_index=index,
                entry=entry,
                stop=stop,
                flow_logic=flow_logic,
                lookahead_bars=lookahead_bars,
            )
        scenarios.append(
            {
                "scenario_id": episode.scenario_id,
                "direction": episode.direction,
                "outcome": outcome,
                "impulse": _bar_payload(
                    bars.iloc[episode.impulse_index]
                ),
                "mitigation": _bar_payload(mitigation),
                "confirmation": _bar_payload(row),
                "zone": {
                    "low": episode.zone_low,
                    "high": episode.zone_high,
                },
                "entry": entry,
                "stop": stop,
                "risk": risk,
                "risk_atr": risk / episode.atr if episode.atr > 0 else None,
                "targets": targets,
            }
        )
        episode = None

    return scenarios


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
        manifest_destination=stage_dir / "fvg_data_manifest.json",
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
        flow_logic=flow_logic,
        trade_start_ns=start_ns,
        trade_end_ns=end_ns,
        lookahead_bars=args.lookahead_bars,
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
            "displacement/FVG mitigation diagnostic; "
            "no orders or hypothetical NAV"
        ),
        "logic": [
            "past-only high-efficiency displacement with matching aggressor flow",
            "literal three-bar non-overlap defines the FVG",
            "counterflow mitigation must touch but not close through the far edge",
            "the next completed signal bar must redisplace beyond the mitigation bar",
            "the state expires after the existing confirmation horizon",
        ],
        "outcome_counts": dict(sorted(outcomes.items())),
        "target_outcome_counts": {
            label: dict(sorted(counts.items()))
            for label, counts in sorted(target_counts.items())
        },
        "scenarios": scenarios,
    }
    write_json_atomic(stage_dir / "fvg_diagnostic.json", payload)
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
