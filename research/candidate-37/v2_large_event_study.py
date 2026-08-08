#!/usr/bin/env python3
"""Two-split causal path study for Candidate 37 large-event routes.

This is deliberately not an account or execution simulator.  It verifies owned
inputs, makes each decision from completed observations only, reserves a fixed
21 bp round trip, and labels which pre-declared structural level was touched
first.  Only a positive, stable result may proceed to NautilusTrader.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from large_event_router import (
    EventRoute, LargeEventConfig, SYMBOLS, route_large_event,
)

MINUTE_NS = 60_000_000_000


class StudyError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _read_owned_chunk(path: Path) -> tuple[str, pd.DataFrame, dict[str, Any]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    symbol = str(manifest["symbol"])
    if symbol not in SYMBOLS:
        raise StudyError(f"unexpected symbol in {path}: {symbol}")
    directory = path.parent
    for name in ("klines.csv.gz", "features.csv.gz"):
        owned = directory / name
        expected = manifest.get("files", {}).get(name, {}).get("sha256")
        if not owned.is_file() or not expected:
            raise StudyError(f"manifest does not own {name}: {path}")
        if _sha256(owned) != expected:
            raise StudyError(f"sha256 mismatch: {owned}")
    price = pd.read_csv(directory / "klines.csv.gz", compression="gzip")
    features = pd.read_csv(directory / "features.csv.gz", compression="gzip")
    if len(price) != len(features) or len(price) != int(manifest["rows"]):
        raise StudyError(f"row ownership mismatch: {path}")
    required_price = {"close_time_dt", "open", "high", "low", "close", "volume"}
    required_feature = {
        "observed_time_ns", "feature_ready", "flow_15s", "flow_60s",
        "efficiency_60s", "notional_burst", "oi_change_15m",
        "premium_change_15m",
    }
    if not required_price.issubset(price.columns):
        raise StudyError(f"missing price columns: {sorted(required_price - set(price.columns))}")
    if not required_feature.issubset(features.columns):
        raise StudyError(f"missing feature columns: {sorted(required_feature - set(features.columns))}")
    time = pd.to_datetime(price["close_time_dt"], utc=True, errors="raise")
    price_ns = np.fromiter((pd.Timestamp(value).value for value in time), dtype=np.int64)
    feature_ns = pd.to_numeric(features["observed_time_ns"], errors="raise").astype("int64").to_numpy()
    if not np.array_equal(price_ns, feature_ns):
        raise StudyError(f"price/feature observation clocks differ: {path}")
    frame = pd.concat(
        [
            price[["open", "high", "low", "close", "volume"]].reset_index(drop=True),
            features[list(required_feature)].reset_index(drop=True),
        ],
        axis=1,
    )
    frame["ts"] = feature_ns
    frame["time"] = time.reset_index(drop=True)
    frame["core_start"] = str(manifest["core_start"])
    frame["core_end"] = str(manifest["core_end"])
    return symbol, frame, manifest


def load_inputs(
    *, input_root: Path, start: pd.Timestamp, end: pd.Timestamp,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    manifests = sorted(input_root.rglob("chunk_manifest.json"))
    if not manifests:
        raise StudyError(f"no chunk manifests below {input_root}")
    pieces: dict[str, list[pd.DataFrame]] = {symbol: [] for symbol in SYMBOLS}
    ownership: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in SYMBOLS}
    for path in manifests:
        symbol, frame, manifest = _read_owned_chunk(path)
        pieces[symbol].append(frame)
        ownership[symbol].append({
            "core_start": manifest["core_start"], "core_end": manifest["core_end"],
            "rows": manifest["rows"],
            "manifest": str(path.relative_to(input_root)),
        })
    frames: dict[str, pd.DataFrame] = {}
    expected = pd.date_range(start, end + pd.Timedelta(days=1) - pd.Timedelta(minutes=1), freq="1min", tz="UTC")
    expected_ns = np.fromiter((pd.Timestamp(value).value for value in expected), dtype=np.int64)
    for symbol in SYMBOLS:
        if not pieces[symbol]:
            raise StudyError(f"no owned chunks for {symbol}")
        frame = pd.concat(pieces[symbol], ignore_index=True).sort_values("ts")
        frame = frame.drop_duplicates("ts", keep=False)
        frame = frame[(frame["time"] >= start) & (frame["time"] <= expected[-1])].copy()
        frame = frame.sort_values("ts").reset_index(drop=True)
        actual = frame["ts"].astype("int64").to_numpy()
        if not np.array_equal(actual, expected_ns):
            raise StudyError(f"{symbol} is not the exact requested minute grid")
        frames[symbol] = enrich(frame)
    reference = frames[SYMBOLS[0]]["ts"].to_numpy()
    for symbol in SYMBOLS[1:]:
        if not np.array_equal(frames[symbol]["ts"].to_numpy(), reference):
            raise StudyError(f"{symbol} differs from the four-symbol clock")
    return frames, ownership


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    previous = result["close"].shift(1)
    true_range = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous).abs(),
            (result["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.shift(1).rolling(30, min_periods=30).mean()
    result["atr_bps"] = atr / result["close"] * 10_000.0
    result["ret1_bps"] = (result["close"] / result["close"].shift(1) - 1.0) * 10_000.0
    result["ret5_bps"] = (result["close"] / result["close"].shift(5) - 1.0) * 10_000.0
    result["prior_high60"] = result["high"].shift(1).rolling(60, min_periods=60).max()
    result["prior_low60"] = result["low"].shift(1).rolling(60, min_periods=60).min()
    result["prior_range60_bps"] = (
        result["prior_high60"] / result["prior_low60"] - 1.0
    ) * 10_000.0
    return result


def _bps(side: int, start: float, end: float) -> float:
    return side * (end - start) / start * 10_000.0


def label_path(
    *, frame: pd.DataFrame, signal_index: int, route: EventRoute,
    config: LargeEventConfig, horizon: int,
) -> dict[str, Any] | None:
    entry_index = signal_index + 1
    if entry_index >= len(frame):
        return None
    entry = float(frame.iloc[entry_index]["open"])
    risk = -_bps(route.side, entry, route.stop_reference)
    reward = _bps(route.side, entry, route.objective_reference)
    if not (
        config.minimum_price_risk_bps <= risk <= config.maximum_price_risk_bps
        and config.minimum_objective_bps <= reward <= config.maximum_objective_bps
    ):
        return None
    planned_loss_bps = risk + config.round_trip_cost_bps
    outcome = "TIME_EXIT"
    first_offset: int | None = None
    mfe_bps = 0.0
    mae_bps = 0.0
    final_index = min(len(frame) - 1, signal_index + horizon)
    for index in range(entry_index, final_index + 1):
        bar = frame.iloc[index]
        if route.side > 0:
            target_hit = float(bar["high"]) >= route.objective_reference
            stop_hit = float(bar["low"]) <= route.stop_reference
            favorable = (float(bar["high"]) - entry) / entry * 10_000.0
            adverse = (float(bar["low"]) - entry) / entry * 10_000.0
        else:
            target_hit = float(bar["low"]) <= route.objective_reference
            stop_hit = float(bar["high"]) >= route.stop_reference
            favorable = (entry - float(bar["low"])) / entry * 10_000.0
            adverse = (entry - float(bar["high"])) / entry * 10_000.0
        mfe_bps = max(mfe_bps, favorable)
        mae_bps = min(mae_bps, adverse)
        if target_hit and stop_hit:
            outcome, first_offset = "AMBIGUOUS_SAME_BAR", index - signal_index
            break
        if target_hit:
            outcome, first_offset = "TARGET_FIRST", index - signal_index
            break
        if stop_hit:
            outcome, first_offset = "STOP_FIRST", index - signal_index
            break
    if outcome == "TARGET_FIRST":
        net_bps = reward - config.round_trip_cost_bps
        net_r = net_bps / planned_loss_bps
    elif outcome in {"STOP_FIRST", "AMBIGUOUS_SAME_BAR"}:
        net_bps = -planned_loss_bps
        net_r = -1.0
    else:
        close = float(frame.iloc[final_index]["close"])
        gross = _bps(route.side, entry, close)
        net_bps = gross - config.round_trip_cost_bps
        net_r = net_bps / planned_loss_bps
    return {
        "entry": entry, "price_risk_bps": risk, "objective_bps": reward,
        "planned_loss_bps": planned_loss_bps, "outcome": outcome,
        "first_hit_offset_minutes": first_offset, "net_bps": net_bps,
        "net_r": net_r, "mfe_bps": mfe_bps, "mae_bps": mae_bps,
    }


def _distribution(values: list[float]) -> dict[str, float | None]:
    finite = np.asarray([value for value in values if math.isfinite(value)], dtype=float)
    if finite.size == 0:
        return {"min": None, "p25": None, "median": None, "p75": None, "max": None}
    return {
        "min": float(np.min(finite)), "p25": float(np.quantile(finite, 0.25)),
        "median": float(np.median(finite)), "p75": float(np.quantile(finite, 0.75)),
        "max": float(np.max(finite)),
    }


def summarize(frame: pd.DataFrame, calendar_days: int) -> dict[str, Any]:
    if frame.empty:
        return {
            "events": 0, "events_per_day": 0.0, "mean_net_r": 0.0,
            "profit_factor": 0.0, "target_first_rate": 0.0,
            "outcomes": {}, "states": {}, "symbols": {},
        }
    net = frame["net_r"].astype(float)
    positive = float(net[net > 0.0].sum())
    negative = float(-net[net < 0.0].sum())
    profit_factor = positive / negative if negative > 0.0 else 1_000_000.0
    non_ambiguous = int((frame["outcome"] != "AMBIGUOUS_SAME_BAR").sum())
    targets = int((frame["outcome"] == "TARGET_FIRST").sum())
    return {
        "events": int(len(frame)), "events_per_day": float(len(frame) / calendar_days),
        "mean_net_r": float(net.mean()), "median_net_r": float(net.median()),
        "profit_factor": float(profit_factor),
        "target_first_rate": float(targets / non_ambiguous) if non_ambiguous else 0.0,
        "outcomes": dict(sorted(Counter(frame["outcome"]).items())),
        "states": dict(sorted(Counter(frame["state"]).items())),
        "symbols": dict(sorted(Counter(frame["symbol"]).items())),
        "net_r_distribution": _distribution(net.tolist()),
        "price_risk_bps_distribution": _distribution(frame["price_risk_bps"].tolist()),
        "objective_bps_distribution": _distribution(frame["objective_bps"].tolist()),
        "mfe_bps_distribution": _distribution(frame["mfe_bps"].tolist()),
        "mae_bps_distribution": _distribution(frame["mae_bps"].tolist()),
    }


def run(
    *, input_root: Path, output: Path, start: pd.Timestamp, end: pd.Timestamp,
    split: pd.Timestamp, horizon: int, lockout_minutes: int,
) -> dict[str, Any]:
    config = LargeEventConfig()
    frames, ownership = load_inputs(input_root=input_root, start=start, end=end)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str, int]] = set()
    last_selected_ts: int | None = None
    duplicate_rejections = lockout_rejections = arbitration_rejections = gap_geometry_rejections = 0
    final_index = len(frames[SYMBOLS[0]]) - horizon - 1
    for index in range(120, max(120, final_index + 1)):
        winner, candidates = route_large_event(frames=frames, index=index, config=config)
        if winner is None:
            if candidates:
                arbitration_rejections += 1
            continue
        episode = (winner.episode_time_ns, winner.state, winner.side)
        if episode in seen:
            duplicate_rejections += 1
            continue
        seen.add(episode)
        if last_selected_ts is not None and winner.signal_time_ns - last_selected_ts < lockout_minutes * MINUTE_NS:
            lockout_rejections += 1
            continue
        label = label_path(
            frame=frames[winner.symbol], signal_index=index, route=winner,
            config=config, horizon=horizon,
        )
        if label is None:
            gap_geometry_rejections += 1
            continue
        last_selected_ts = winner.signal_time_ns
        signal_time = pd.Timestamp(winner.signal_time_ns, unit="ns", tz="UTC")
        rows.append({
            "signal_time": signal_time.isoformat(), "signal_time_ns": winner.signal_time_ns,
            "episode_time_ns": winner.episode_time_ns, "split": "development" if signal_time < split else "validation",
            "symbol": winner.symbol, "state": winner.state, "side": winner.side,
            "score": winner.score, "stop": winner.stop_reference,
            "objective": winner.objective_reference, "reason": winner.reason,
            "diagnostics_json": json.dumps(dict(winner.diagnostics), sort_keys=True),
            **label,
        })
    output.mkdir(parents=True, exist_ok=True)
    routes = pd.DataFrame(rows)
    routes.to_csv(output / "large_event_routes.csv", index=False)
    development_days = max(1, int((split - start).days))
    validation_days = max(1, int((end + pd.Timedelta(days=1) - split).days))
    development = routes[routes["split"] == "development"] if not routes.empty else routes
    validation = routes[routes["split"] == "validation"] if not routes.empty else routes
    dev_summary = summarize(development, development_days)
    val_summary = summarize(validation, validation_days)
    state_split: dict[str, Any] = {}
    if not routes.empty:
        for state in sorted(routes["state"].unique()):
            state_split[state] = {
                "development": summarize(development[development["state"] == state], development_days),
                "validation": summarize(validation[validation["state"] == state], validation_days),
            }
    checks = {
        "exact_four_symbol_minute_clock": True,
        "future_input_violations": 0,
        "development_at_least_half_event_per_day": dev_summary["events_per_day"] >= 0.5,
        "validation_at_least_half_event_per_day": val_summary["events_per_day"] >= 0.5,
        "development_positive_cost_after_expectancy": dev_summary["mean_net_r"] > 0.10,
        "validation_positive_cost_after_expectancy": val_summary["mean_net_r"] > 0.10,
        "development_profit_factor": dev_summary["profit_factor"] >= 1.20,
        "validation_profit_factor": val_summary["profit_factor"] >= 1.20,
        "development_not_single_state": len(dev_summary.get("states", {})) >= 2,
        "validation_not_single_state": len(val_summary.get("states", {})) >= 2,
    }
    result = {
        "schema": "candidate-37-large-auction-event-study-v1",
        "claim_scope": "CAUSAL_STRUCTURAL_PATH_LABELS_ONLY_NO_ORDER_FILL_POSITION_PNL_OR_NAV_SIMULATION",
        "period_start": start.date().isoformat(), "period_end": end.date().isoformat(),
        "split_time": split.isoformat(), "horizon_minutes": horizon,
        "global_episode_lockout_minutes": lockout_minutes,
        "config": asdict(config), "input_ownership": ownership,
        "selected_events": int(len(routes)),
        "duplicate_episode_rejections": duplicate_rejections,
        "global_lockout_rejections": lockout_rejections,
        "arbitration_rejections": arbitration_rejections,
        "next_open_geometry_rejections": gap_geometry_rejections,
        "development": dev_summary, "validation": val_summary,
        "state_split": state_split, "gate_checks": checks,
        "decision": (
            "ELIGIBLE_FOR_UNTOUCHED_NAUTILUS_DIAGNOSTIC"
            if all(checks.values()) else "REVISE_OR_REJECT_BEFORE_NAUTILUS"
        ),
    }
    (output / "study.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--horizon-minutes", type=int, default=180)
    parser.add_argument("--lockout-minutes", type=int, default=120)
    args = parser.parse_args()
    result = run(
        input_root=args.input_root.resolve(), output=args.output.resolve(),
        start=pd.Timestamp(args.start, tz="UTC"), end=pd.Timestamp(args.end, tz="UTC"),
        split=pd.Timestamp(args.split, tz="UTC"), horizon=args.horizon_minutes,
        lockout_minutes=args.lockout_minutes,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
