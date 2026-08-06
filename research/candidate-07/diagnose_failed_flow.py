#!/usr/bin/env python3
"""Diagnose accepted continuation after a flow-qualified absorption thesis fails.

Evidence only: this script creates no orders and computes no hypothetical NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from data_flow import load_flow_bundle
from smc_ict_4.manifest import write_json_atomic

NS_PER_MINUTE = 60_000_000_000


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object in {path}")
    return value


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def aggregate_flow(frame: pd.DataFrame, minutes: int, flow_period: int) -> pd.DataFrame:
    work = frame.copy()
    work["bucket"] = [index // minutes for index in range(len(work))]
    grouped = work.groupby("bucket", sort=True)
    bars = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        taker_buy_volume=("taker_buy_base", "sum"),
    )
    bars["timestamp_ns"] = grouped.apply(
        lambda part: int(part.index[-1].value),
        include_groups=False,
    )
    bars = bars.reset_index(drop=True)
    bars["delta"] = 2.0 * bars["taker_buy_volume"] - bars["volume"]
    bars["imbalance"] = (bars["delta"] / bars["volume"].where(bars["volume"] > 0)).fillna(0.0)
    prior_abs_delta = bars["delta"].abs().shift(1)
    mean = prior_abs_delta.rolling(flow_period, min_periods=flow_period).mean()
    std = prior_abs_delta.rolling(flow_period, min_periods=flow_period).std(ddof=0)
    bars["flow_z"] = ((bars["delta"].abs() - mean) / std.where(std > 1e-12)).fillna(0.0)
    previous_close = bars["close"].shift(1)
    true_range = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous_close).abs(),
            (bars["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["atr"] = true_range.shift(1).rolling(24, min_periods=24).mean()
    bars["timestamp"] = pd.to_datetime(bars["timestamp_ns"], unit="ns", utc=True)
    return bars


def first_reason(events: list[dict[str, Any]], *reasons: str) -> dict[str, Any] | None:
    accepted = set(reasons)
    return next((event for event in events if event.get("reason_code") in accepted), None)


def source_from_events(scenario_id: str, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    contact = next(
        (event for event in events if str(event.get("reason_code", "")).endswith("AGGRESSION_ABSORBED")),
        None,
    )
    confirmed = first_reason(events, "OPPOSITE_AGGRESSOR_DISPLACEMENT")
    if contact is None or confirmed is None:
        return None
    source_direction = "SHORT" if str(contact["reason_code"]).startswith("UPPER_") else "LONG"
    continuation_direction = "LONG" if source_direction == "SHORT" else "SHORT"
    route = first_reason(events, "CAUSAL_FLOW_ROUTE_READY")
    geometry = first_reason(events, "UNTRADEABLE_FLOW_GEOMETRY")
    delayed = first_reason(
        events,
        "FLOW_DELAYED_ENTRY_STOP_OUTSIDE_STATE",
        "FLOW_DELAYED_ENTRY_STOP_TOO_TIGHT",
        "DELAYED_ENTRY_RR_ERODED",
        "FLOW_DELAYED_ENTRY_GEOMETRY_INVALID",
    )
    opened = first_reason(events, "NAUTILUS_POSITION_OPENED")
    closed = first_reason(events, "NAUTILUS_POSITION_CLOSED")
    route_details = dict(route.get("details") or {}) if route else {}
    geometry_details = dict(geometry.get("details") or {}) if geometry else {}
    stop = route_details.get("stop", geometry_details.get("stop"))
    entry = route.get("reference_price") if route else geometry_details.get("entry")
    if stop is None or entry is None:
        return None
    stop, entry = float(stop), float(entry)
    source_risk = abs(stop - entry)
    if source_risk <= 0:
        return None
    status = "ROUTE_READY_NOT_FILLED"
    monitor_ns = int(route["event_time_ns"]) if route else int(geometry["event_time_ns"])
    if geometry:
        status = f"GEOMETRY_{geometry_details.get('geometry_reason', 'REJECTED')}"
        monitor_ns = int(geometry["event_time_ns"])
    if delayed:
        status = str(delayed["reason_code"])
        monitor_ns = int(delayed["event_time_ns"])
    if opened and closed:
        close_price = float(closed["reference_price"])
        if abs(close_price - stop) <= max(1.0, 0.03 * source_risk):
            status = "ACTUAL_STRUCTURAL_STOP"
            monitor_ns = int(closed["event_time_ns"])
        else:
            status = "REVERSAL_POSITION_CLOSED_OTHER"
            monitor_ns = int(closed["event_time_ns"])
    return {
        "scenario_id": scenario_id,
        "source_direction": source_direction,
        "continuation_direction": continuation_direction,
        "status": status,
        "monitor_start_ns": monitor_ns,
        "liquidity_level": float(contact["reference_price"]),
        "acceptance_level": stop,
        "source_entry": entry,
        "source_risk": source_risk,
        "source_atr": float((confirmed.get("details") or {})["atr"]),
        "source_confirmation_imbalance": float(
            (confirmed.get("details") or {})["confirmation_imbalance"]
        ),
        "sweep_extreme": geometry_details.get("sweep_extreme"),
        "opposing_external": geometry_details.get("opposing_external"),
        "geometry_reason": geometry_details.get("geometry_reason"),
    }


def confirmation_index(
    path: pd.DataFrame,
    source: Mapping[str, Any],
    flow_logic: Mapping[str, Any],
) -> tuple[int, str] | None:
    direction = str(source["continuation_direction"])
    acceptance = float(source["acceptance_level"])
    pool = float(source["liquidity_level"])
    rows = list(path.itertuples(index=False))
    for index, row in enumerate(rows):
        outside = row.close > acceptance if direction == "LONG" else row.close < acceptance
        same_flow = (
            row.imbalance >= float(flow_logic["absorption_min_imbalance"])
            if direction == "LONG"
            else row.imbalance <= -float(flow_logic["absorption_min_imbalance"])
        )
        directional_body = row.close > row.open if direction == "LONG" else row.close < row.open
        displacement = row.atr == row.atr and abs(row.close - row.open) >= float(
            flow_logic["confirmation_body_atr"]
        ) * row.atr
        if not (
            outside
            and same_flow
            and row.flow_z >= float(flow_logic["absorption_flow_z"])
            and directional_body
            and displacement
        ):
            continue
        if index + 1 >= len(rows):
            return index, "DISPLACEMENT_ONLY"
        following = rows[index + 1]
        held = (
            following.low > pool and following.close > acceptance
            if direction == "LONG"
            else following.high < pool and following.close < acceptance
        )
        if held:
            return index + 1, "DISPLACEMENT_THEN_POOL_HOLD"
    return None


def target_outcome(path: pd.DataFrame, direction: str, stop: float, target: float) -> dict[str, Any]:
    for row in path.itertuples(index=False):
        stop_hit = row.low <= stop if direction == "LONG" else row.high >= stop
        target_hit = row.high >= target if direction == "LONG" else row.low <= target
        if stop_hit and target_hit:
            return {"outcome": "AMBIGUOUS_SAME_BAR", "timestamp_ns": int(row.timestamp_ns)}
        if stop_hit:
            return {"outcome": "STOP", "timestamp_ns": int(row.timestamp_ns)}
        if target_hit:
            return {"outcome": "TARGET", "timestamp_ns": int(row.timestamp_ns)}
    return {"outcome": "UNRESOLVED", "timestamp_ns": None}


def diagnose(
    source: Mapping[str, Any],
    bars: pd.DataFrame,
    flow_logic: Mapping[str, Any],
    lookahead_minutes: int,
) -> dict[str, Any]:
    result = dict(source)
    start_ns = int(source["monitor_start_ns"])
    path = bars[
        (bars["timestamp_ns"] > start_ns)
        & (bars["timestamp_ns"] <= start_ns + lookahead_minutes * NS_PER_MINUTE)
    ].copy()
    result["monitor_start"] = datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc).isoformat()
    result["path_bars"] = [
        {
            "timestamp_ns": int(row.timestamp_ns),
            "timestamp": row.timestamp.isoformat(),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "imbalance": float(row.imbalance),
            "flow_z": float(row.flow_z),
            "atr": None if row.atr != row.atr else float(row.atr),
        }
        for row in path.head(12).itertuples(index=False)
    ]
    if path.empty:
        result["confirmation"] = None
        return result
    direction = str(source["continuation_direction"])
    acceptance = float(source["acceptance_level"])
    result["mfe_from_acceptance"] = (
        float(path["high"].max()) - acceptance
        if direction == "LONG"
        else acceptance - float(path["low"].min())
    )
    result["pool_reclaimed"] = bool(
        (path["close"] <= float(source["liquidity_level"])).any()
        if direction == "LONG"
        else (path["close"] >= float(source["liquidity_level"])).any()
    )
    found = confirmation_index(path, source, flow_logic)
    if found is None:
        result["confirmation"] = None
        return result
    index, kind = found
    row = path.iloc[index]
    entry = float(row["close"])
    atr = float(row["atr"]) if row["atr"] == row["atr"] else float(source["source_atr"])
    buffer = float(flow_logic["stop_buffer_atr"]) * atr
    pool = float(source["liquidity_level"])
    stop = pool - buffer if direction == "LONG" else pool + buffer
    risk = entry - stop if direction == "LONG" else stop - entry
    result["confirmation"] = {
        "type": kind,
        "timestamp_ns": int(row["timestamp_ns"]),
        "timestamp": row["timestamp"].isoformat(),
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "risk_atr": risk / atr if atr > 0 else None,
        "imbalance": float(row["imbalance"]),
        "flow_z": float(row["flow_z"]),
        "atr": atr,
    }
    if risk <= 0:
        result["confirmation"]["geometry"] = "NONPOSITIVE_RISK"
        return result
    future = path.iloc[index + 1 :]
    targets: dict[str, float] = {
        f"{rr:.1f}R": entry + risk * rr if direction == "LONG" else entry - risk * rr
        for rr in (1.0, 1.5, 2.0, 3.0)
    }
    history = bars[bars["timestamp_ns"] < int(row["timestamp_ns"])]
    for label, count in (("PRIOR_12H", 144), ("PRIOR_24H", 288)):
        window = history.tail(count)
        level = float(window["high"].max()) if direction == "LONG" else float(window["low"].min())
        if level > entry if direction == "LONG" else level < entry:
            targets[label] = level
    opposing = source.get("opposing_external")
    if opposing is not None:
        width = abs(pool - float(opposing))
        for fraction in (0.25, 0.50):
            level = pool + width * fraction if direction == "LONG" else pool - width * fraction
            if level > entry if direction == "LONG" else level < entry:
                targets[f"RANGE_EXTENSION_{fraction:.2f}"] = level
    result["targets"] = {
        label: {
            "price": level,
            "rr": abs(level - entry) / risk,
            **target_outcome(future, direction, stop, level),
        }
        for label, level in targets.items()
    }
    return result


def run(args: argparse.Namespace) -> int:
    config = read_json(args.config)
    stage_dir = args.output.resolve() / args.stage
    metrics = read_json(stage_dir / "metrics.json")
    start = date.fromisoformat(str(metrics["period"]["start"]))
    end = date.fromisoformat(str(metrics["period"]["end_exclusive"]))
    bundle = load_flow_bundle(
        symbol=str(config["symbol"]),
        trade_start=start,
        trade_end=end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=stage_dir / "failed_flow_data_manifest.json",
    )
    flow_logic = dict(config["flow_logic"])
    bars = aggregate_flow(
        bundle.frame,
        int(flow_logic["signal_minutes"]),
        int(flow_logic["flow_period"]),
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in read_events(stage_dir / "events.jsonl"):
        scenario_id = str(event.get("scenario_id", ""))
        if scenario_id.startswith("c07f-"):
            grouped[scenario_id].append(event)
    sources = []
    for scenario_id, events in grouped.items():
        events.sort(key=lambda item: int(item["event_time_ns"]))
        source = source_from_events(scenario_id, events)
        if source and source["status"] != "REVERSAL_POSITION_CLOSED_OTHER":
            sources.append(source)
    scenarios = [
        diagnose(source, bars, flow_logic, args.lookahead_minutes)
        for source in sources
    ]
    status_counts = Counter(str(item["status"]) for item in scenarios)
    confirmation_counts = Counter(
        str(item["confirmation"]["type"])
        for item in scenarios
        if isinstance(item.get("confirmation"), Mapping)
    )
    target_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for item in scenarios:
        for label, target in (item.get("targets") or {}).items():
            target_counts[label][str(target["outcome"])] += 1
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "purpose": "diagnostic only; no hypothetical NAV or order simulation",
        "continuation_sequence": [
            "source absorption route fails or is not tradable",
            "completed five-minute bar accepts beyond structural invalidation with original-direction aggressor flow",
            "next completed bar holds outside the original liquidity pool",
        ],
        "lookahead_minutes": args.lookahead_minutes,
        "sources": len(scenarios),
        "status_counts": dict(sorted(status_counts.items())),
        "confirmation_counts": dict(sorted(confirmation_counts.items())),
        "target_outcome_counts": {
            label: dict(sorted(counts.items()))
            for label, counts in sorted(target_counts.items())
        },
        "scenarios": scenarios,
    }
    write_json_atomic(stage_dir / "failed_flow_diagnostic.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    result.add_argument("--stage", default="week-1")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--data-root", type=Path, default=Path(".research-data/candidate-07"))
    result.add_argument("--lookahead-minutes", type=int, default=120)
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
