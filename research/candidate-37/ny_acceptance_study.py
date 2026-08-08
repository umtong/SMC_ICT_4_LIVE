#!/usr/bin/env python3
"""Continuous four-symbol causal screen for the frozen NY acceptance policy.

This is a promotion screen, not a replacement matching/account engine.  It uses
next-minute-open entries, conservative stop-before-target ordering on ambiguous
one-minute bars, a fixed 21 bp round trip, one global position, and exact 3% of
current NAV as planned loss.  A positive result must still move to NautilusTrader.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ny_acceptance import (
    AcceptanceSignal,
    NYAcceptanceConfig,
    build_levels,
    first_accepted_break,
)

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class StudyError(RuntimeError):
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


def load_inputs(
    input_root: Path,
    data_start: pd.Timestamp,
    data_end: pd.Timestamp,
) -> tuple[dict[str, pd.DataFrame], dict[str, list[dict[str, Any]]]]:
    pieces: dict[str, list[pd.DataFrame]] = {symbol: [] for symbol in SYMBOLS}
    ownership: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in SYMBOLS}
    for manifest_path in sorted(input_root.rglob("month_manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        symbol = str(manifest["symbol"])
        if symbol not in pieces:
            continue
        data_path = manifest_path.parent / "minute_state.csv.gz"
        expected = manifest.get("files", {}).get("minute_state.csv.gz", {}).get("sha256")
        if not data_path.is_file() or not expected or _sha256(data_path) != expected:
            raise StudyError(f"unowned or corrupt data: {manifest_path}")
        frame = pd.read_csv(data_path, compression="gzip")
        if len(frame) != int(manifest["rows"]):
            raise StudyError(f"row count mismatch: {manifest_path}")
        pieces[symbol].append(frame)
        ownership[symbol].append({
            "core_start": manifest["core_start"],
            "core_end": manifest["core_end"],
            "rows": manifest["rows"],
            "manifest": str(manifest_path.relative_to(input_root)),
        })
    expected_time = pd.date_range(data_start, data_end, freq="1min", tz="UTC")
    expected_ns = np.fromiter((pd.Timestamp(item).value for item in expected_time), dtype=np.int64)
    frames: dict[str, pd.DataFrame] = {}
    required = {"time", "open", "high", "low", "close", "metrics_ready", "basis_ready"}
    for symbol in SYMBOLS:
        if not pieces[symbol]:
            raise StudyError(f"missing data for {symbol}")
        frame = pd.concat(pieces[symbol], ignore_index=True)
        if not required.issubset(frame.columns):
            raise StudyError(f"{symbol} missing {sorted(required - set(frame.columns))}")
        frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="raise")
        frame = frame[(frame["time"] >= data_start) & (frame["time"] <= data_end)].sort_values("time")
        if frame["time"].duplicated().any():
            raise StudyError(f"duplicate minute clock for {symbol}")
        frame = frame.reset_index(drop=True)
        actual_ns = np.fromiter((pd.Timestamp(item).value for item in frame["time"]), dtype=np.int64)
        if not np.array_equal(actual_ns, expected_ns):
            raise StudyError(f"{symbol} is not the exact requested minute grid")
        frame["feature_ready"] = _truth(frame["metrics_ready"]) & _truth(frame["basis_ready"])
        frames[symbol] = frame
    reference = frames[SYMBOLS[0]]["time"].to_numpy()
    for symbol in SYMBOLS[1:]:
        if not np.array_equal(frames[symbol]["time"].to_numpy(), reference):
            raise StudyError(f"unaligned four-symbol clock: {symbol}")
    return frames, ownership


def _bps(side: int, start: float, end: float) -> float:
    return side * (end - start) / start * 10_000.0


def label_trade(
    frame: pd.DataFrame,
    signal: AcceptanceSignal,
    config: NYAcceptanceConfig,
) -> dict[str, Any]:
    entry_index = signal.signal_index + 1
    entry = float(frame.iloc[entry_index]["open"])
    risk = -_bps(signal.side, entry, signal.stop)
    reward = _bps(signal.side, entry, signal.target)
    if not config.minimum_price_risk_bps <= risk <= config.maximum_price_risk_bps:
        raise StudyError("next-open geometry violated the frozen risk contract")
    end_index = min(len(frame) - 1, entry_index + config.horizon_minutes)
    outcome = "TIME_EXIT"
    exit_index = end_index
    exit_price = float(frame.iloc[end_index]["close"])
    mfe = 0.0
    mae = 0.0
    for index in range(entry_index, end_index + 1):
        bar = frame.iloc[index]
        if signal.side > 0:
            stop_hit = float(bar["low"]) <= signal.stop
            target_hit = float(bar["high"]) >= signal.target
            favorable = (float(bar["high"]) - entry) / entry * 10_000.0
            adverse = (float(bar["low"]) - entry) / entry * 10_000.0
        else:
            stop_hit = float(bar["high"]) >= signal.stop
            target_hit = float(bar["low"]) <= signal.target
            favorable = (entry - float(bar["low"])) / entry * 10_000.0
            adverse = (entry - float(bar["high"])) / entry * 10_000.0
        mfe = max(mfe, favorable)
        mae = min(mae, adverse)
        # Conservative ordering: stop wins every same-bar ambiguity.
        if stop_hit:
            outcome, exit_index, exit_price = "STOP_FIRST", index, signal.stop
            break
        if target_hit:
            outcome, exit_index, exit_price = "TARGET_FIRST", index, signal.target
            break
    gross_bps = _bps(signal.side, entry, exit_price)
    net_bps = gross_bps - config.round_trip_cost_bps
    planned_loss_bps = risk + config.round_trip_cost_bps
    return {
        "entry_index": entry_index,
        "exit_index": exit_index,
        "entry_time": pd.Timestamp(frame.iloc[entry_index]["time"]),
        "exit_time": pd.Timestamp(frame.iloc[exit_index]["time"]),
        "entry": entry,
        "risk_bps": risk,
        "reward_bps": reward,
        "planned_loss_bps": planned_loss_bps,
        "outcome": outcome,
        "gross_bps": gross_bps,
        "net_bps": net_bps,
        "net_r": net_bps / planned_loss_bps,
        "mfe_bps": mfe,
        "mae_bps": mae,
    }


def summarize(frame: pd.DataFrame, days: int) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0, "trades_per_day": 0.0, "mean_net_r": 0.0, "profit_factor": 0.0, "win_rate": 0.0, "outcomes": {}}
    net = frame["net_r"].astype(float)
    profit = float(net[net > 0.0].sum())
    loss = float(-net[net < 0.0].sum())
    return {
        "trades": int(len(frame)),
        "trades_per_day": float(len(frame) / max(1, days)),
        "mean_net_r": float(net.mean()),
        "median_net_r": float(net.median()),
        "profit_factor": float(profit / loss) if loss > 0.0 else 1_000_000.0,
        "win_rate": float((net > 0.0).mean()),
        "outcomes": dict(sorted(Counter(frame["outcome"]).items())),
        "levels": dict(sorted(Counter(frame["level_name"]).items())),
        "symbols": dict(sorted(Counter(frame["symbol"]).items())),
    }


def run(
    input_root: Path,
    output: Path,
    evaluation_start: date,
    evaluation_end: date,
    split_day: date,
) -> dict[str, Any]:
    config = NYAcceptanceConfig()
    data_start = pd.Timestamp(evaluation_start - timedelta(days=1), tz="UTC")
    data_end = pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC") - pd.Timedelta(minutes=1)
    frames, ownership = load_inputs(input_root, data_start, data_end)
    time_index = {symbol: {time: index for index, time in enumerate(frame["time"])} for symbol, frame in frames.items()}
    rows: list[dict[str, Any]] = []
    occupied_until: pd.Timestamp | None = None
    local_day = evaluation_start
    ambiguity_rejections = overlap_rejections = 0
    while local_day <= evaluation_end:
        candidates: list[AcceptanceSignal] = []
        for symbol in SYMBOLS:
            frame = frames[symbol]
            for level in build_levels(frame, local_day):
                signal = first_accepted_break(
                    symbol=symbol, frame=frame, local_day=local_day,
                    level=level, config=config,
                )
                if signal is not None:
                    candidates.append(signal)
        if candidates:
            first_time = min(item.signal_time for item in candidates)
            same_time = [item for item in candidates if item.signal_time == first_time]
            same_time.sort(key=lambda item: (-item.score, item.symbol, item.level_name))
            winner = same_time[0]
            if len(same_time) > 1 and math.isclose(same_time[0].score, same_time[1].score, abs_tol=1e-12):
                ambiguity_rejections += 1
            elif occupied_until is not None and winner.signal_time <= occupied_until:
                overlap_rejections += 1
            else:
                frame = frames[winner.symbol]
                label = label_trade(frame, winner, config)
                occupied_until = label["exit_time"]
                rows.append({
                    "local_day": local_day.isoformat(),
                    "split": "development_holdout" if local_day < split_day else "validation_holdout",
                    "symbol": winner.symbol,
                    "level_name": winner.level_name,
                    "side": winner.side,
                    "signal_time": winner.signal_time.isoformat(),
                    "boundary": winner.boundary,
                    "stop": winner.stop,
                    "target": winner.target,
                    "score": winner.score,
                    **{key: (value.isoformat() if isinstance(value, pd.Timestamp) else value) for key, value in label.items()},
                })
        local_day += timedelta(days=1)
    output.mkdir(parents=True, exist_ok=True)
    trades = pd.DataFrame(rows)
    trades.to_csv(output / "trades.csv", index=False)
    nav = 100_000.0
    peak = nav
    max_drawdown = 0.0
    nav_rows: list[dict[str, Any]] = []
    if not trades.empty:
        for _, trade in trades.sort_values("entry_time").iterrows():
            nav *= 1.0 + 0.03 * float(trade["net_r"])
            peak = max(peak, nav)
            max_drawdown = max(max_drawdown, 1.0 - nav / peak)
            nav_rows.append({"exit_time": trade["exit_time"], "nav": nav})
    pd.DataFrame(nav_rows).to_csv(output / "nav.csv", index=False)
    calendar_days = (evaluation_end - evaluation_start).days + 1
    geo = (nav / 100_000.0) ** (1.0 / calendar_days) - 1.0 if nav > 0.0 else -1.0
    dev = trades[trades["split"] == "development_holdout"] if not trades.empty else trades
    val = trades[trades["split"] == "validation_holdout"] if not trades.empty else trades
    dev_days = max(1, (split_day - evaluation_start).days)
    val_days = max(1, (evaluation_end - split_day).days + 1)
    dev_summary = summarize(dev, dev_days)
    val_summary = summarize(val, val_days)
    gates = {
        "exact_four_symbol_minute_clock": True,
        "future_input_violations": 0,
        "one_global_position": overlap_rejections == 0,
        "development_positive_expectancy": dev_summary["mean_net_r"] > 0.0,
        "validation_positive_expectancy": val_summary["mean_net_r"] > 0.0,
        "development_profit_factor": dev_summary["profit_factor"] >= 1.20,
        "validation_profit_factor": val_summary["profit_factor"] >= 1.20,
        "positive_continuous_nav": nav > 100_000.0,
        "drawdown_below_twenty_percent": max_drawdown <= 0.20,
    }
    result = {
        "schema": "candidate-37-ny-accepted-auction-untouched-v1",
        "claim_scope": "CAUSAL_PATH_AND_CONTINUOUS_RISK_SCREEN_NO_NAUTILUS_ORDER_FILL_OR_MARGIN_CLAIM",
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end": evaluation_end.isoformat(),
        "split_day": split_day.isoformat(),
        "calendar_days": calendar_days,
        "config": asdict(config),
        "input_ownership": ownership,
        "trades": int(len(trades)),
        "development": dev_summary,
        "validation": val_summary,
        "starting_nav": 100_000.0,
        "final_nav": nav,
        "geometric_daily_growth": geo,
        "maximum_drawdown": max_drawdown,
        "ambiguity_rejections": ambiguity_rejections,
        "overlap_rejections": overlap_rejections,
        "gates": gates,
        "decision": "PROMOTE_TO_NAUTILUS_SHORT_EXECUTION" if all(gates.values()) else "REJECT_OR_REDESIGN",
    }
    (output / "study.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--split-day", required=True)
    args = parser.parse_args()
    result = run(
        args.input_root.resolve(), args.output.resolve(),
        date.fromisoformat(args.evaluation_start),
        date.fromisoformat(args.evaluation_end),
        date.fromisoformat(args.split_day),
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
