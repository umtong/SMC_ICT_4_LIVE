#!/usr/bin/env python3
"""Train the fixed Candidate 05 v46 selective evidence model.

Input consists only of completed NautilusTrader v26 training and validation
runs which end before every frozen evaluation week.  No evaluation result is
read and no threshold is searched.  A fixed logistic evidence model and fixed
0.75 probability threshold are admitted only when the chronologically later
pre-evaluation validation set has enough independent trades, high precision,
positive net PnL and distributed gross profit.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterator
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


FEATURE_NAMES = (
    "directional_flow_15s",
    "directional_flow_60s",
    "directional_flow_3m",
    "flow_tail_improvement",
    "directional_depth",
    "efficiency_60s",
    "log_notional_burst",
    "log_absorption_60s",
    "oi_change_15m",
    "penetration_atr",
    "log_pool_age",
    "target_net_r",
    "branch_sponsored",
    "branch_retrace",
    "branch_second_touch",
    "branch_balance",
    "branch_breakaway",
)
THRESHOLD = 0.75
L2 = 0.25
ITERATIONS = 1200


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, dict):
                result.update(_flatten(child, name))
            elif not isinstance(child, list):
                result[name] = child
    return result


def _find(flat: dict[str, Any], *names: str) -> Any:
    lowered = {key.lower(): value for key, value in flat.items()}
    for name in names:
        target = name.lower()
        if target in lowered:
            return lowered[target]
        suffix = f".{target}"
        matches = [value for key, value in lowered.items() if key.endswith(suffix)]
        if matches:
            return matches[-1]
    return None


def _side(flat: dict[str, Any]) -> int | None:
    value = _find(flat, "side", "entry_side", "position_side", "order_side")
    if value is None:
        return None
    text = str(value).upper()
    if text in {"BUY", "LONG", "1", "1.0", "ORDERSIDE.BUY"}:
        return 1
    if text in {"SELL", "SHORT", "-1", "-1.0", "ORDERSIDE.SELL"}:
        return -1
    number = _number(value)
    return 1 if number > 0 else -1 if number < 0 else None


def _pnl(flat: dict[str, Any]) -> float:
    for name in (
        "net_pnl",
        "realized_pnl",
        "realised_pnl",
        "pnl",
        "profit_loss",
        "net_profit",
        "profit",
    ):
        value = _number(_find(flat, name))
        if math.isfinite(value):
            return value
    return math.nan


def _timestamp(flat: dict[str, Any]) -> int:
    for name in (
        "position_close_ts",
        "close_ts",
        "exit_ts",
        "closed_ts",
        "ts_closed",
        "ts_event",
        "timestamp",
    ):
        raw = _find(flat, name)
        value = _number(raw)
        if math.isfinite(value):
            integer = int(value)
            if integer < 10_000_000_000:
                return integer * 1_000_000_000
            if integer < 10_000_000_000_000:
                return integer * 1_000_000
            if integer < 10_000_000_000_000_000:
                return integer * 1_000
            return integer
        if isinstance(raw, str):
            try:
                return int(pd.Timestamp(raw, tz="UTC").value)
            except Exception:
                pass
    return 0


def _feature_row(flat: dict[str, Any]) -> tuple[list[float], str, int] | None:
    side = _side(flat)
    pnl = _pnl(flat)
    if side is None or not math.isfinite(pnl):
        return None
    flow15 = _number(_find(flat, "flow_15s"))
    flow60 = _number(_find(flat, "flow_60s"))
    flow3m = _number(_find(flat, "flow_3m"))
    depth = _number(_find(flat, "depth_imbalance_1"))
    efficiency = _number(_find(flat, "efficiency_60s"))
    burst = _number(_find(flat, "notional_burst"))
    absorption = _number(_find(flat, "absorption_60s"))
    oi = _number(
        _find(
            flat,
            "oi_change_15m",
            "oi_change_sweep_to_confirmation",
            "oi_change_sweep_to_selection",
        ),
    )
    penetration = _number(_find(flat, "penetration_atr"))
    pool_age = _number(_find(flat, "pool_age_minutes", "pool_age_bars"))
    target_r = _number(
        _find(flat, "target_net_r", "net_r", "expected_net_r", "minimum_net_r"),
    )
    branch = str(_find(flat, "branch", "scenario_branch", "entry_branch") or "").lower()
    values = [
        side * flow15 if math.isfinite(flow15) else math.nan,
        side * flow60 if math.isfinite(flow60) else math.nan,
        side * flow3m if math.isfinite(flow3m) else math.nan,
        side * (flow15 - flow60) if math.isfinite(flow15) and math.isfinite(flow60) else math.nan,
        side * depth if math.isfinite(depth) else math.nan,
        efficiency,
        math.log1p(max(burst, 0.0)) if math.isfinite(burst) else math.nan,
        math.log1p(max(absorption, 0.0)) if math.isfinite(absorption) else math.nan,
        oi,
        penetration,
        math.log1p(max(pool_age, 0.0)) if math.isfinite(pool_age) else math.nan,
        target_r,
        float("sponsor" in branch or "choch" in branch),
        float("retrace" in branch),
        float("second" in branch or "touch" in branch),
        float("balance" in branch or "position_build" in branch),
        float("breakaway" in branch),
    ]
    scenario_id = str(_find(flat, "scenario_id", "trade_id", "position_id") or "")
    return values, scenario_id, _timestamp(flat)


def _records_from_json(path: Path) -> list[dict[str, Any]]:
    try:
        if path.suffix == ".jsonl":
            values = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        else:
            values = [json.loads(path.read_text())]
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        for item in _walk(value):
            flat = _flatten(item)
            parsed = _feature_row(flat)
            if parsed is None:
                continue
            _, scenario_id, ts = parsed
            key = scenario_id or f"{path}:{ts}:{len(rows)}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(flat)
    return rows


def _event_records(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pattern in ("closed_scenarios.json", "*closed*scenario*.json", "scenario_events.jsonl"):
        for path in root.rglob(pattern):
            rows.extend(_records_from_json(path))
    # De-duplicate globally, preferring the richest flattened record.
    selected: dict[str, dict[str, Any]] = {}
    for index, flat in enumerate(rows):
        scenario_id = str(_find(flat, "scenario_id", "trade_id", "position_id") or f"anon-{index}")
        current = selected.get(scenario_id)
        if current is None or len(flat) > len(current):
            selected[scenario_id] = flat
    return list(selected.values())


def _matrix(root: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    features: list[list[float]] = []
    labels: list[float] = []
    pnl: list[float] = []
    timestamps: list[int] = []
    for flat in _event_records(root):
        parsed = _feature_row(flat)
        if parsed is None:
            continue
        values, _, ts = parsed
        result = _pnl(flat)
        features.append(values)
        labels.append(float(result > 0.0))
        pnl.append(result)
        timestamps.append(ts)
    if not features:
        return (
            np.empty((0, len(FEATURE_NAMES))),
            np.empty(0),
            np.empty(0),
            np.empty(0, dtype=np.int64),
        )
    order = np.argsort(np.asarray(timestamps, dtype=np.int64))
    return (
        np.asarray(features, dtype=float)[order],
        np.asarray(labels, dtype=float)[order],
        np.asarray(pnl, dtype=float)[order],
        np.asarray(timestamps, dtype=np.int64)[order],
    )


def _fit(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    means = np.nanmedian(x, axis=0)
    means = np.where(np.isfinite(means), means, 0.0)
    filled = np.where(np.isfinite(x), x, means)
    scales = np.nanmedian(np.abs(filled - means), axis=0) * 1.4826
    scales = np.where(np.isfinite(scales) & (scales > 1e-9), scales, 1.0)
    z = (filled - means) / scales
    weights = np.zeros(z.shape[1], dtype=float)
    intercept = math.log((float(y.sum()) + 1.0) / (float(len(y) - y.sum()) + 1.0))
    for iteration in range(ITERATIONS):
        logits = np.clip(intercept + z @ weights, -30.0, 30.0)
        probability = 1.0 / (1.0 + np.exp(-logits))
        residual = probability - y
        rate = 0.08 / math.sqrt(1.0 + iteration / 100.0)
        intercept -= rate * float(residual.mean())
        gradient = z.T @ residual / len(y) + L2 * weights / len(y)
        weights -= rate * gradient
    return weights, intercept, means, scales


def _predict(x: np.ndarray, weights: np.ndarray, intercept: float, means: np.ndarray, scales: np.ndarray) -> np.ndarray:
    filled = np.where(np.isfinite(x), x, means)
    logits = np.clip(intercept + ((filled - means) / scales) @ weights, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-logits))


def _assessment(probability: np.ndarray, y: np.ndarray, pnl: np.ndarray, timestamps: np.ndarray) -> dict[str, Any]:
    selected = probability >= THRESHOLD
    count = int(selected.sum())
    selected_pnl = pnl[selected]
    wins = int(y[selected].sum())
    gross_profit = float(selected_pnl[selected_pnl > 0.0].sum())
    gross_loss = float(-selected_pnl[selected_pnl < 0.0].sum())
    months = sorted(
        {
            datetime.fromtimestamp(int(ts) / 1_000_000_000, tz=timezone.utc).strftime("%Y-%m")
            for ts in timestamps[selected]
            if int(ts) > 0
        },
    )
    largest_share = (
        float(selected_pnl.max()) / gross_profit
        if count and gross_profit > 0.0
        else 1.0
    )
    return {
        "trades": count,
        "wins": wins,
        "win_rate": wins / count if count else 0.0,
        "net_pnl": float(selected_pnl.sum()) if count else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else math.inf if gross_profit > 0.0 else 0.0,
        "largest_winner_share": largest_share,
        "active_months": months,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-root", type=Path, required=True)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    x_train, y_train, pnl_train, ts_train = _matrix(args.train_root)
    x_valid, y_valid, pnl_valid, ts_valid = _matrix(args.validation_root)
    if len(y_train) < 60 or len(y_valid) < 20:
        raise RuntimeError(
            f"insufficient pre-evaluation trades train={len(y_train)} validation={len(y_valid)}",
        )
    weights, intercept, means, scales = _fit(x_train, y_train)
    train_assessment = _assessment(
        _predict(x_train, weights, intercept, means, scales),
        y_train,
        pnl_train,
        ts_train,
    )
    valid_assessment = _assessment(
        _predict(x_valid, weights, intercept, means, scales),
        y_valid,
        pnl_valid,
        ts_valid,
    )
    validation_pass = (
        valid_assessment["trades"] >= 12
        and valid_assessment["wins"] >= 9
        and valid_assessment["win_rate"] >= 0.75
        and valid_assessment["net_pnl"] > 0.0
        and valid_assessment["profit_factor"] >= 2.0
        and valid_assessment["largest_winner_share"] <= 0.50
        and len(valid_assessment["active_months"]) >= 2
    )
    all_x = np.concatenate([x_train, x_valid], axis=0)
    all_y = np.concatenate([y_train, y_valid], axis=0)
    final_weights, final_intercept, final_means, final_scales = _fit(all_x, all_y)
    payload = {
        "schema": "candidate-05-v46-selective-evidence-v1",
        "training_contract": {
            "evaluation_data_used": False,
            "fixed_probability_threshold": THRESHOLD,
            "l2": L2,
            "iterations": ITERATIONS,
            "feature_names": list(FEATURE_NAMES),
        },
        "training_trades": int(len(y_train)),
        "validation_trades": int(len(y_valid)),
        "training_assessment": train_assessment,
        "validation_assessment": valid_assessment,
        "validation_pass": bool(validation_pass),
        "model": {
            "feature_names": list(FEATURE_NAMES),
            "means": final_means.tolist(),
            "scales": final_scales.tolist(),
            "weights": final_weights.tolist(),
            "intercept": float(final_intercept),
            "threshold": THRESHOLD,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not validation_pass:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
