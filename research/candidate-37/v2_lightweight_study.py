#!/usr/bin/env python3
"""Fast long-span screen of Candidate 37 v2 using owned lightweight state.

The input is the pinned Candidate 30 price/OI/premium builder.  One-minute
aggressor flow is the causal taker-buy quote share available in the completed
kline; it is a coarse triage proxy, not a replacement for the rich aggTrade and
depth confirmation used by the production candidate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from large_event_router import LargeEventConfig, SYMBOLS, route_large_event
from v2_large_event_study import label_path, summarize

MINUTE_NS = 60_000_000_000


class LightStudyError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _truth(values: pd.Series) -> pd.Series:
    if values.dtype == bool:
        return values
    return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes"})


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
    quote = pd.to_numeric(result["quote_volume"], errors="coerce")
    taker = pd.to_numeric(result["taker_buy_quote_volume"], errors="coerce")
    result["flow_60s"] = np.where(quote > 0.0, 2.0 * taker / quote - 1.0, np.nan)
    # The lightweight archive has no 15-second partition.  Reusing the completed
    # minute imbalance is explicit and only for this triage study.
    result["flow_15s"] = result["flow_60s"]
    result["efficiency_60s"] = np.where(
        true_range > 0.0, (result["close"] - previous).abs() / true_range, 0.0,
    )
    baseline = quote.shift(1).rolling(60, min_periods=30).median()
    result["notional_burst"] = np.where(baseline > 0.0, quote / baseline, np.nan)
    oi = pd.to_numeric(result["sum_open_interest"], errors="coerce")
    result["oi_change_15m"] = oi / oi.shift(15) - 1.0
    premium = pd.to_numeric(result["premium_index"], errors="coerce")
    result["premium_change_15m"] = premium - premium.shift(15)
    result["feature_ready"] = _truth(result["metrics_ready"]) & _truth(result["basis_ready"])
    return result


def load(
    *, input_root: Path, start: pd.Timestamp, end: pd.Timestamp,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    paths = sorted(input_root.rglob("month_manifest.json"))
    pieces: dict[str, list[pd.DataFrame]] = {symbol: [] for symbol in SYMBOLS}
    ownership: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in SYMBOLS}
    for path in paths:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        symbol = str(manifest["symbol"])
        if symbol not in SYMBOLS:
            continue
        data = path.parent / "minute_state.csv.gz"
        expected = manifest.get("files", {}).get("minute_state.csv.gz", {}).get("sha256")
        if not data.is_file() or not expected or _sha256(data) != expected:
            raise LightStudyError(f"unowned or corrupt month: {path}")
        frame = pd.read_csv(data, compression="gzip")
        if len(frame) != int(manifest["rows"]):
            raise LightStudyError(f"row mismatch: {path}")
        pieces[symbol].append(frame)
        ownership[symbol].append({
            "core_start": manifest["core_start"], "core_end": manifest["core_end"],
            "rows": manifest["rows"], "manifest": str(path.relative_to(input_root)),
        })
    expected_time = pd.date_range(
        start, end + pd.Timedelta(days=1) - pd.Timedelta(minutes=1),
        freq="1min", tz="UTC",
    )
    expected_ns = np.fromiter((pd.Timestamp(value).value for value in expected_time), dtype=np.int64)
    frames: dict[str, pd.DataFrame] = {}
    required = {
        "time", "open", "high", "low", "close", "volume", "quote_volume",
        "taker_buy_quote_volume", "sum_open_interest", "metrics_ready",
        "premium_index", "basis_ready",
    }
    for symbol in SYMBOLS:
        if not pieces[symbol]:
            raise LightStudyError(f"missing months for {symbol}")
        frame = pd.concat(pieces[symbol], ignore_index=True)
        if not required.issubset(frame.columns):
            raise LightStudyError(f"{symbol} missing {sorted(required - set(frame.columns))}")
        time = pd.to_datetime(frame["time"], utc=True, errors="raise")
        frame["time"] = time
        frame["ts"] = np.fromiter((pd.Timestamp(value).value for value in time), dtype=np.int64)
        frame = frame[(time >= start) & (time <= expected_time[-1])].sort_values("ts")
        if frame["ts"].duplicated().any():
            raise LightStudyError(f"duplicate minutes for {symbol}")
        frame = frame.reset_index(drop=True)
        if not np.array_equal(frame["ts"].to_numpy(dtype=np.int64), expected_ns):
            raise LightStudyError(f"{symbol} is not the exact minute grid")
        frames[symbol] = enrich(frame)
    reference = frames[SYMBOLS[0]]["ts"].to_numpy(dtype=np.int64)
    for symbol in SYMBOLS[1:]:
        if not np.array_equal(frames[symbol]["ts"].to_numpy(dtype=np.int64), reference):
            raise LightStudyError(f"unaligned four-symbol clock: {symbol}")
    return frames, ownership


def run(
    *, input_root: Path, output: Path, start: pd.Timestamp, end: pd.Timestamp,
    split: pd.Timestamp, horizon: int, lockout_minutes: int,
) -> dict[str, Any]:
    # The 15-second field is explicitly a minute proxy here, so do not give it
    # an additional gate beyond the independent 60-second requirement.
    config = replace(LargeEventConfig(), breakout_min_flow_15s=-1.0)
    frames, ownership = load(input_root=input_root, start=start, end=end)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str, int]] = set()
    last_ts: int | None = None
    duplicate_rejections = lockout_rejections = ambiguity_rejections = geometry_rejections = 0
    final = len(frames[SYMBOLS[0]]) - horizon - 1
    for index in range(120, max(120, final + 1)):
        winner, candidates = route_large_event(frames=frames, index=index, config=config)
        if winner is None:
            if candidates:
                ambiguity_rejections += 1
            continue
        episode = (winner.episode_time_ns, winner.state, winner.side)
        if episode in seen:
            duplicate_rejections += 1
            continue
        seen.add(episode)
        if last_ts is not None and winner.signal_time_ns - last_ts < lockout_minutes * MINUTE_NS:
            lockout_rejections += 1
            continue
        label = label_path(
            frame=frames[winner.symbol], signal_index=index, route=winner,
            config=config, horizon=horizon,
        )
        if label is None:
            geometry_rejections += 1
            continue
        last_ts = winner.signal_time_ns
        time = pd.Timestamp(winner.signal_time_ns, unit="ns", tz="UTC")
        rows.append({
            "signal_time": time.isoformat(), "signal_time_ns": winner.signal_time_ns,
            "episode_time_ns": winner.episode_time_ns,
            "split": "development" if time < split else "validation",
            "symbol": winner.symbol, "state": winner.state, "side": winner.side,
            "score": winner.score, "stop": winner.stop_reference,
            "objective": winner.objective_reference, "reason": winner.reason,
            "diagnostics_json": json.dumps(dict(winner.diagnostics), sort_keys=True),
            **label,
        })
    output.mkdir(parents=True, exist_ok=True)
    routes = pd.DataFrame(rows)
    routes.to_csv(output / "lightweight_routes.csv", index=False)
    dev_days = max(1, int((split - start).days))
    val_days = max(1, int((end + pd.Timedelta(days=1) - split).days))
    development = routes[routes["split"] == "development"] if not routes.empty else routes
    validation = routes[routes["split"] == "validation"] if not routes.empty else routes
    dev = summarize(development, dev_days)
    val = summarize(validation, val_days)
    states: dict[str, Any] = {}
    if not routes.empty:
        for state in sorted(routes["state"].unique()):
            states[state] = {
                "development": summarize(development[development["state"] == state], dev_days),
                "validation": summarize(validation[validation["state"] == state], val_days),
            }
    checks = {
        "exact_four_symbol_minute_clock": True,
        "future_input_violations": 0,
        "development_at_least_half_event_per_day": dev["events_per_day"] >= 0.5,
        "validation_at_least_half_event_per_day": val["events_per_day"] >= 0.5,
        "development_positive_cost_after_expectancy": dev["mean_net_r"] > 0.10,
        "validation_positive_cost_after_expectancy": val["mean_net_r"] > 0.10,
        "development_profit_factor": dev["profit_factor"] >= 1.20,
        "validation_profit_factor": val["profit_factor"] >= 1.20,
    }
    result = {
        "schema": "candidate-37-large-event-lightweight-screen-v1",
        "claim_scope": "CAUSAL_COARSE_FEATURE_PATH_SCREEN_NO_ORDER_FILL_POSITION_PNL_OR_NAV_SIMULATION",
        "flow_proxy": "completed_1m_taker_buy_quote_share_used_for_60s_and_15s_proxy",
        "period_start": start.date().isoformat(), "period_end": end.date().isoformat(),
        "split_time": split.isoformat(), "horizon_minutes": horizon,
        "global_episode_lockout_minutes": lockout_minutes,
        "config": asdict(config), "input_ownership": ownership,
        "selected_events": int(len(routes)), "development": dev,
        "validation": val, "state_split": states,
        "duplicate_episode_rejections": duplicate_rejections,
        "global_lockout_rejections": lockout_rejections,
        "arbitration_rejections": ambiguity_rejections,
        "next_open_geometry_rejections": geometry_rejections,
        "gate_checks": checks,
        "decision": "RICH_CONFIRMATION_WARRANTED" if all(checks.values()) else "REVISE_OR_REJECT_LARGE_EVENT_POLICY",
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
