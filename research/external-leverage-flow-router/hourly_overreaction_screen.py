#!/usr/bin/env python3
"""Fixed causal screen for 1/2/4-hour cryptocurrency overreaction.

External research reports negative first-order autocorrelation at one-, two-,
and four-hour frequencies and stronger percentage reversal after larger moves.
This script transfers that mechanism without parameter search:

* completed hourly return magnitude >= max(100 bps, 2.5 shifted rolling sigma);
* completed hourly quote volume >= 2x its shifted 30-day median;
* one event per four-hour causal episode;
* entry at the strictly later next-hour open;
* exit at the close of hour 1, 2, or 4;
* fixed 20 bp adverse round-trip hurdle.

This is an economic-space screen, not a backtest.  Any surviving family must
still receive a complete state/entry/stop/target policy and NautilusTrader
execution validation.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cross_asset_transfer_screen_runner import robust_read_kline
from features import download_checked


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
HORIZONS = (1, 2, 4)
ABS_RETURN_FLOOR_BPS = 100.0
SIGMA_MULTIPLE = 2.5
VOLUME_BURST_MIN = 2.0
ROLLING_BASELINE_HOURS = 24 * 30
MIN_BASELINE_HOURS = 24 * 10
EPISODE_COOLDOWN_HOURS = 4
ROUND_TRIP_HURDLE_BPS = 20.0


def load_hourly(symbol: str, start: date, end: date, cache: Path) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    day = start
    from datetime import timedelta
    while day <= end:
        archive, _checksum, raw = download_checked("klines", symbol, day, cache / symbol)
        frames.append(robust_read_kline(archive))
        evidence.append({
            "endpoint": raw.endpoint,
            "day": raw.day,
            "archive": raw.archive,
            "checksum": raw.checksum,
            "size_bytes": raw.size_bytes,
            "sha256": raw.sha256,
        })
        day += timedelta(days=1)
    minute = pd.concat(frames, ignore_index=True).sort_values("open_time_dt")
    minute = minute.set_index("open_time_dt")
    hourly = minute.resample("1h", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        quote_volume=("quote_volume", "sum"),
        minute_count=("close", "count"),
    )
    hourly = hourly[hourly["minute_count"] >= 59].copy()
    hourly["return_bps"] = np.log(hourly["close"] / hourly["open"]) * 10_000.0
    sigma = hourly["return_bps"].shift(1).rolling(
        ROLLING_BASELINE_HOURS,
        min_periods=MIN_BASELINE_HOURS,
    ).std(ddof=1)
    volume_baseline = hourly["quote_volume"].shift(1).rolling(
        ROLLING_BASELINE_HOURS,
        min_periods=MIN_BASELINE_HOURS,
    ).median()
    hourly["return_sigma"] = hourly["return_bps"] / sigma.replace(0.0, np.nan)
    hourly["volume_burst"] = hourly["quote_volume"] / volume_baseline.replace(0.0, np.nan)
    return hourly, evidence


def independent_events(frame: pd.DataFrame) -> pd.Series:
    threshold = np.maximum(ABS_RETURN_FLOOR_BPS, SIGMA_MULTIPLE * frame["return_sigma"].abs() * 0.0)
    # The expression above intentionally avoids using current sigma in price
    # units. The fixed condition below uses standardized magnitude directly.
    raw = (
        (frame["return_bps"].abs() >= ABS_RETURN_FLOOR_BPS)
        & (frame["return_sigma"].abs() >= SIGMA_MULTIPLE)
        & (frame["volume_burst"] >= VOLUME_BURST_MIN)
    )
    selected = pd.Series(False, index=frame.index)
    last = None
    for timestamp in frame.index[raw.fillna(False)]:
        if last is None or (timestamp - last).total_seconds() >= EPISODE_COOLDOWN_HOURS * 3600:
            selected.loc[timestamp] = True
            last = timestamp
    return selected


def summarize(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {"count": 0, "mean_net_bps": None, "median_net_bps": None,
                "win_rate_after_hurdle": None, "p10_net_bps": None,
                "p90_net_bps": None, "largest_winner_share": None}
    winners = clean[clean > 0.0]
    winner_sum = float(winners.sum())
    return {
        "count": int(len(clean)),
        "mean_net_bps": float(clean.mean()),
        "median_net_bps": float(clean.median()),
        "win_rate_after_hurdle": float((clean > 0.0).mean()),
        "p10_net_bps": float(clean.quantile(0.10)),
        "p90_net_bps": float(clean.quantile(0.90)),
        "largest_winner_share": float(winners.max() / winner_sum) if winner_sum > 0.0 else None,
    }


def run(start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    all_events: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    promising: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frame, raw = load_hourly(symbol, start, end, cache)
        evidence.extend(raw)
        mask = independent_events(frame)
        direction = -np.sign(frame["return_bps"])
        entry = frame["open"].shift(-1)
        symbol_result = {"events": int(mask.sum()), "horizons": {}}
        for horizon in HORIZONS:
            exit_price = frame["close"].shift(-horizon)
            net = direction * np.log(exit_price / entry) * 10_000.0 - ROUND_TRIP_HURDLE_BPS
            stats = summarize(net[mask])
            symbol_result["horizons"][str(horizon)] = stats
            if (
                stats["count"] >= 8
                and stats["mean_net_bps"] is not None and stats["mean_net_bps"] > 0.0
                and stats["median_net_bps"] is not None and stats["median_net_bps"] > 0.0
                and stats["win_rate_after_hurdle"] is not None and stats["win_rate_after_hurdle"] >= 0.55
                and (stats["largest_winner_share"] is None or stats["largest_winner_share"] <= 0.60)
            ):
                promising.append({"symbol": symbol, "horizon_hours": horizon, **stats})
        results[symbol] = symbol_result
        for timestamp in frame.index[mask.fillna(False)]:
            row = {
                "timestamp": timestamp.isoformat(),
                "symbol": symbol,
                "parent_return_bps": float(frame.loc[timestamp, "return_bps"]),
                "parent_return_sigma": float(frame.loc[timestamp, "return_sigma"]),
                "parent_volume_burst": float(frame.loc[timestamp, "volume_burst"]),
                "trade_direction": int(direction.loc[timestamp]),
            }
            for horizon in HORIZONS:
                exit_price = frame["close"].shift(-horizon)
                net = direction * np.log(exit_price / entry) * 10_000.0 - ROUND_TRIP_HURDLE_BPS
                row[f"net_{horizon}h_bps"] = float(net.loc[timestamp])
            all_events.append(row)

    pd.DataFrame(all_events).to_csv(output / "hourly_overreaction_events.csv", index=False)
    report = {
        "schema": "fixed-hourly-overreaction-screen-v1",
        "development_period": [start.isoformat(), end.isoformat()],
        "symbols": list(SYMBOLS),
        "parent": {
            "absolute_return_floor_bps": ABS_RETURN_FLOOR_BPS,
            "shifted_rolling_sigma_multiple": SIGMA_MULTIPLE,
            "shifted_volume_burst_min": VOLUME_BURST_MIN,
            "rolling_baseline_hours": ROLLING_BASELINE_HOURS,
            "minimum_baseline_hours": MIN_BASELINE_HOURS,
            "episode_cooldown_hours": EPISODE_COOLDOWN_HOURS,
        },
        "entry": "strictly later next-hour open, opposite completed parent return",
        "horizons_hours": list(HORIZONS),
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "results": results,
        "promising_fixed_cells": promising,
        "interpretation": "A pass identifies economic space only; it is not a trading result.",
    }
    (output / "hourly_overreaction_screen.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "raw_evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(date.fromisoformat(args.start), date.fromisoformat(args.end), args.cache, args.output)
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
