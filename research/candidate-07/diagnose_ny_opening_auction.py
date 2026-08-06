#!/usr/bin/env python3
"""Diagnose the weekday New York cash-open dealing-range auction.

The first 30 minutes after 09:30 America/New_York form a public, completed
dealing range. From 10:00 to 12:00 local time the first causal contact with
either boundary is classified as one of two mutually exclusive states:

- directional acceptance: displacement closes beyond the boundary with matching
  aggressor flow, then the next completed five-minute bar holds outside;
- failed auction: aggressive penetration is rejected back inside the range,
  then the next completed five-minute bar displaces in the opposite direction.

The script emits market-path evidence only. It creates no orders, fills, PnL,
cash ledger, or hypothetical NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, time
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from data_flow import load_flow_bundle
from diagnose_failed_flow import aggregate_flow, read_json, target_outcome
from smc_ict_4.manifest import write_json_atomic


NEW_YORK = ZoneInfo("America/New_York")
OPEN_RANGE_START = time(9, 30)
OPEN_RANGE_END = time(10, 0)
AUCTION_END = time(12, 0)


def _local_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    return value.tz_convert(NEW_YORK)


def _between(value: time, start: time, end: time) -> bool:
    return start <= value < end


def _bar_payload(row: pd.Series) -> dict[str, Any]:
    return {
        "timestamp_ns": int(row["timestamp_ns"]),
        "timestamp": row["timestamp"].isoformat(),
        "new_york_time": _local_timestamp(row["timestamp"]).isoformat(),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"]),
        "imbalance": float(row["imbalance"]),
        "flow_z": float(row["flow_z"]),
        "atr": None if pd.isna(row["atr"]) else float(row["atr"]),
    }


def _evaluate_targets(
    future: pd.DataFrame,
    *,
    direction: str,
    entry: float,
    stop: float,
    levels: dict[str, float],
    minimum_rr: float,
) -> dict[str, dict[str, Any]]:
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for label, target in levels.items():
        favorable = target > entry if direction == "LONG" else target < entry
        if not favorable:
            continue
        rr = abs(target - entry) / risk
        record: dict[str, Any] = {"price": target, "rr": rr}
        if label not in {"1.0R", "1.5R", "2.0R"} and rr < minimum_rr:
            record.update({"outcome": "BELOW_MINIMUM_RR", "timestamp_ns": None})
        else:
            record.update(target_outcome(future, direction, stop, target))
        result[label] = record
    return result


def _acceptance_candidate(
    bars: pd.DataFrame,
    index: int,
    *,
    side: str,
    boundary: float,
    range_low: float,
    range_high: float,
    flow_logic: dict[str, Any],
    session_end_index: int,
) -> dict[str, Any] | None:
    row = bars.iloc[index]
    atr = float(row["atr"])
    penetration = float(flow_logic["sweep_min_atr"]) * atr
    direction = "LONG" if side == "UPPER" else "SHORT"
    accepted = (
        float(row["close"]) >= boundary + penetration
        if direction == "LONG"
        else float(row["close"]) <= boundary - penetration
    )
    directional_body = (
        float(row["close"]) > float(row["open"])
        if direction == "LONG"
        else float(row["close"]) < float(row["open"])
    )
    matching_flow = (
        float(row["imbalance"]) >= float(flow_logic["absorption_min_imbalance"])
        if direction == "LONG"
        else float(row["imbalance"]) <= -float(flow_logic["absorption_min_imbalance"])
    )
    displacement = abs(float(row["close"]) - float(row["open"])) >= (
        float(flow_logic["confirmation_body_atr"]) * atr
    )
    if not (
        accepted
        and directional_body
        and matching_flow
        and float(row["flow_z"]) >= float(flow_logic["absorption_flow_z"])
        and displacement
    ):
        return None
    if index + 1 > session_end_index:
        return None
    hold = bars.iloc[index + 1]
    tolerance = float(flow_logic["reclaim_buffer_atr"]) * atr
    held = (
        float(hold["low"]) >= boundary - tolerance
        and float(hold["close"]) > boundary
        if direction == "LONG"
        else float(hold["high"]) <= boundary + tolerance
        and float(hold["close"]) < boundary
    )
    if not held:
        return {
            "kind": "OPEN_RANGE_ACCEPTANCE",
            "direction": direction,
            "outcome": "ACCEPTANCE_RECLAIMED_NEXT_BAR",
            "contact": _bar_payload(row),
            "confirmation": _bar_payload(hold),
        }

    entry = float(hold["close"])
    stop = (
        boundary - float(flow_logic["stop_buffer_atr"]) * atr
        if direction == "LONG"
        else boundary + float(flow_logic["stop_buffer_atr"]) * atr
    )
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0:
        return {
            "kind": "OPEN_RANGE_ACCEPTANCE",
            "direction": direction,
            "outcome": "NONPOSITIVE_RISK",
            "contact": _bar_payload(row),
            "confirmation": _bar_payload(hold),
        }
    width = range_high - range_low
    levels = {
        "1.0R": entry + risk if direction == "LONG" else entry - risk,
        "1.5R": entry + 1.5 * risk if direction == "LONG" else entry - 1.5 * risk,
        "2.0R": entry + 2.0 * risk if direction == "LONG" else entry - 2.0 * risk,
        "OPEN_RANGE_EXTENSION_0.50": (
            range_high + 0.5 * width
            if direction == "LONG"
            else range_low - 0.5 * width
        ),
        "OPEN_RANGE_EXTENSION_1.00": (
            range_high + width
            if direction == "LONG"
            else range_low - width
        ),
    }
    future = bars.iloc[index + 2 : session_end_index + 1]
    return {
        "kind": "OPEN_RANGE_ACCEPTANCE",
        "direction": direction,
        "outcome": "OUTSIDE_ACCEPTANCE_HELD",
        "contact": _bar_payload(row),
        "confirmation": _bar_payload(hold),
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "risk_atr": risk / atr,
        "targets": _evaluate_targets(
            future,
            direction=direction,
            entry=entry,
            stop=stop,
            levels=levels,
            minimum_rr=float(flow_logic["minimum_rr"]),
        ),
    }


def _rejection_candidate(
    bars: pd.DataFrame,
    index: int,
    *,
    side: str,
    boundary: float,
    range_low: float,
    range_high: float,
    flow_logic: dict[str, Any],
    session_end_index: int,
) -> dict[str, Any] | None:
    row = bars.iloc[index]
    atr = float(row["atr"])
    penetration = float(flow_logic["sweep_min_atr"]) * atr
    bar_range = max(1e-12, float(row["high"]) - float(row["low"]))
    if side == "UPPER":
        penetrated = float(row["high"]) >= boundary + penetration
        reclaimed = float(row["close"]) < boundary - (
            float(flow_logic["reclaim_buffer_atr"]) * atr
        )
        wick = (
            float(row["high"]) - max(float(row["open"]), float(row["close"]))
        ) / bar_range
        attack_flow = float(row["imbalance"]) >= float(
            flow_logic["absorption_min_imbalance"]
        )
        direction = "SHORT"
        extreme = float(row["high"])
    else:
        penetrated = float(row["low"]) <= boundary - penetration
        reclaimed = float(row["close"]) > boundary + (
            float(flow_logic["reclaim_buffer_atr"]) * atr
        )
        wick = (
            min(float(row["open"]), float(row["close"])) - float(row["low"])
        ) / bar_range
        attack_flow = float(row["imbalance"]) <= -float(
            flow_logic["absorption_min_imbalance"]
        )
        direction = "LONG"
        extreme = float(row["low"])
    if not (
        penetrated
        and reclaimed
        and wick >= float(flow_logic["sweep_wick_fraction"])
        and attack_flow
        and float(row["flow_z"]) >= float(flow_logic["absorption_flow_z"])
    ):
        return None
    if index + 1 > session_end_index:
        return None
    confirm = bars.iloc[index + 1]
    confirmation_flow = (
        float(confirm["imbalance"]) <= -float(
            flow_logic["confirmation_min_imbalance"]
        )
        if direction == "SHORT"
        else float(confirm["imbalance"]) >= float(
            flow_logic["confirmation_min_imbalance"]
        )
    )
    directional_body = (
        float(confirm["close"]) < float(confirm["open"])
        if direction == "SHORT"
        else float(confirm["close"]) > float(confirm["open"])
    )
    displacement = abs(float(confirm["close"]) - float(confirm["open"])) >= (
        float(flow_logic["confirmation_body_atr"]) * atr
    )
    inside = (
        float(confirm["close"]) < boundary
        if direction == "SHORT"
        else float(confirm["close"]) > boundary
    )
    if not (confirmation_flow and directional_body and displacement and inside):
        return {
            "kind": "OPEN_RANGE_FAILED_AUCTION",
            "direction": direction,
            "outcome": "REJECTION_NOT_CONFIRMED",
            "contact": _bar_payload(row),
            "confirmation": _bar_payload(confirm),
        }

    entry = float(confirm["close"])
    stop = (
        extreme + float(flow_logic["stop_buffer_atr"]) * atr
        if direction == "SHORT"
        else extreme - float(flow_logic["stop_buffer_atr"]) * atr
    )
    risk = stop - entry if direction == "SHORT" else entry - stop
    if risk <= 0:
        return {
            "kind": "OPEN_RANGE_FAILED_AUCTION",
            "direction": direction,
            "outcome": "NONPOSITIVE_RISK",
            "contact": _bar_payload(row),
            "confirmation": _bar_payload(confirm),
        }
    midpoint = 0.5 * (range_high + range_low)
    levels = {
        "1.0R": entry - risk if direction == "SHORT" else entry + risk,
        "1.5R": entry - 1.5 * risk if direction == "SHORT" else entry + 1.5 * risk,
        "2.0R": entry - 2.0 * risk if direction == "SHORT" else entry + 2.0 * risk,
        "OPEN_RANGE_MIDPOINT": midpoint,
        "OPPOSITE_OPEN_RANGE_BOUNDARY": (
            range_low if direction == "SHORT" else range_high
        ),
    }
    future = bars.iloc[index + 2 : session_end_index + 1]
    return {
        "kind": "OPEN_RANGE_FAILED_AUCTION",
        "direction": direction,
        "outcome": "FAILED_AUCTION_CONFIRMED",
        "contact": _bar_payload(row),
        "confirmation": _bar_payload(confirm),
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "risk_atr": risk / atr,
        "targets": _evaluate_targets(
            future,
            direction=direction,
            entry=entry,
            stop=stop,
            levels=levels,
            minimum_rr=float(flow_logic["minimum_rr"]),
        ),
    }


def diagnose(
    bars: pd.DataFrame,
    *,
    flow_logic: dict[str, Any],
    trade_start_ns: int,
    trade_end_ns: int,
) -> list[dict[str, Any]]:
    work = bars.copy()
    work["ny_timestamp"] = work["timestamp"].map(_local_timestamp)
    work["ny_date"] = work["ny_timestamp"].dt.date
    work["ny_time"] = work["ny_timestamp"].dt.time
    scenarios: list[dict[str, Any]] = []

    for ny_date, session in work.groupby("ny_date", sort=True):
        if ny_date.weekday() >= 5:
            continue
        opening = session[
            session["ny_time"].map(
                lambda value: _between(value, OPEN_RANGE_START, OPEN_RANGE_END)
            )
        ]
        auction = session[
            session["ny_time"].map(
                lambda value: _between(value, OPEN_RANGE_END, AUCTION_END)
            )
        ]
        if len(opening) != 6 or auction.empty:
            continue
        if not (
            trade_start_ns <= int(opening.iloc[0]["timestamp_ns"]) < trade_end_ns
        ):
            continue
        range_high = float(opening["high"].max())
        range_low = float(opening["low"].min())
        range_width = range_high - range_low
        if range_width <= 0:
            continue
        session_end_index = int(auction.index[-1])
        consumed = {"UPPER": False, "LOWER": False}
        completed: list[dict[str, Any]] = []

        for index in auction.index:
            row = work.loc[index]
            atr = row["atr"]
            if pd.isna(atr) or float(atr) <= 0:
                continue
            penetration = float(flow_logic["sweep_min_atr"]) * float(atr)
            contacts = []
            if not consumed["UPPER"] and float(row["high"]) >= range_high + penetration:
                contacts.append(("UPPER", range_high))
            if not consumed["LOWER"] and float(row["low"]) <= range_low - penetration:
                contacts.append(("LOWER", range_low))
            if len(contacts) > 1:
                consumed["UPPER"] = True
                consumed["LOWER"] = True
                continue
            if not contacts:
                continue
            side, boundary = contacts[0]
            consumed[side] = True
            acceptance = _acceptance_candidate(
                work,
                int(index),
                side=side,
                boundary=boundary,
                range_low=range_low,
                range_high=range_high,
                flow_logic=flow_logic,
                session_end_index=session_end_index,
            )
            rejection = _rejection_candidate(
                work,
                int(index),
                side=side,
                boundary=boundary,
                range_low=range_low,
                range_high=range_high,
                flow_logic=flow_logic,
                session_end_index=session_end_index,
            )
            candidate = acceptance or rejection
            if candidate is None:
                continue
            candidate.update(
                {
                    "scenario_id": (
                        f"c07ny-{ny_date.isoformat()}-{side.lower()}"
                    ),
                    "new_york_date": ny_date.isoformat(),
                    "liquidity_side": side,
                    "opening_range": {
                        "start": opening.iloc[0]["ny_timestamp"].isoformat(),
                        "end_exclusive": OPEN_RANGE_END.isoformat(),
                        "high": range_high,
                        "low": range_low,
                        "width": range_width,
                    },
                }
            )
            completed.append(candidate)

        # One global slot: select only the earliest completed confirmation in a
        # New York morning. A later opposite-side event is a new auction but
        # cannot be assumed simultaneously tradable.
        if completed:
            completed.sort(
                key=lambda item: int(item["confirmation"]["timestamp_ns"])
            )
            scenarios.append(completed[0])

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
        manifest_destination=stage_dir / "ny_opening_data_manifest.json",
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
    )
    outcomes = Counter(str(item["outcome"]) for item in scenarios)
    target_counts: dict[str, Counter[str]] = defaultdict(Counter)
    by_kind: dict[str, Counter[str]] = defaultdict(Counter)
    for item in scenarios:
        by_kind[str(item["kind"])][str(item["outcome"])] += 1
        for label, target in (item.get("targets") or {}).items():
            target_counts[label][str(target["outcome"])] += 1
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "purpose": (
            "New York cash-open dealing-range auction diagnostic; "
            "no orders or hypothetical NAV"
        ),
        "timezone": "America/New_York",
        "opening_range": "09:30 <= local time < 10:00, weekdays",
        "auction_window": "10:00 <= local time < 12:00",
        "logic": [
            "the completed first 30 minutes form the dealing range",
            "the first causal contact with each boundary consumes that boundary",
            "directional close plus matching aggressor flow is routed to acceptance",
            "aggressive penetration plus close back inside is routed to failed auction",
            "one earliest completed route per New York morning preserves the global slot",
        ],
        "outcome_counts": dict(sorted(outcomes.items())),
        "by_kind_outcomes": {
            kind: dict(sorted(counts.items()))
            for kind, counts in sorted(by_kind.items())
        },
        "target_outcome_counts": {
            label: dict(sorted(counts.items()))
            for label, counts in sorted(target_counts.items())
        },
        "scenarios": scenarios,
    }
    write_json_atomic(stage_dir / "ny_opening_diagnostic.json", payload)
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
