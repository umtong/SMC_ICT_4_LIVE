#!/usr/bin/env python3
"""Fixed 15-minute auction acceptance/failure screen across four crypto markets.

The screen converts two practical external ideas into causal event families:

* an important range broken by a wide, high-volume bar that closes near its
  extreme should show continued price discovery, especially after the first
  shallow defense of the old boundary;
* a high-volume sweep that closes back inside the old range is a failed auction
  and should travel toward the opposite side of value.

The prior four-hour balance is made only from completed 15-minute bars.  Every
entry occurs at a strictly later bar open.  This is an economic-space screen,
not a backtester; any passing family must still be implemented in
NautilusTrader with actual fills, one global position, risk sizing, and a
continuous account.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, timedelta
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE05 = HERE.parent / "candidate-05"
sys.path.insert(0, str(CANDIDATE05))
sys.path.insert(0, str(HERE))

from features import download_checked
from cross_asset_transfer_screen_fixed import robust_read_kline


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BAR_MINUTES = 15
PRIOR_BALANCE_BARS = 16
DEFENSE_MAX_BARS = 4
PARENT_COOLDOWN_BARS = 16
HORIZONS_BARS = (1, 2, 4, 8, 16)
ROUND_TRIP_HURDLE_BPS = 20.0
MIN_RANGE_BURST = 1.50
MIN_VOLUME_BURST = 2.00
MIN_ACCEPTANCE_BREAK_ATR = 0.10
MIN_SWEEP_ATR = 0.25
MIN_CLOSE_LOCATION = 0.75
MAX_FAILED_CLOSE_LOCATION = 0.50
MAX_DEFENSE_INSIDE_ATR = 0.10
MIN_DEFENSE_RETRACE_PARENT_RANGE = 0.20


def _load_symbol(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    day = start
    while day <= end:
        archive, _checksum, raw = download_checked(
            "klines", symbol, day, cache / symbol,
        )
        frames.append(robust_read_kline(archive))
        evidence.append(asdict(raw))
        day += timedelta(days=1)

    minute = pd.concat(frames, ignore_index=True).sort_values("open_time_dt")
    if minute["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate minute for {symbol}")
    minute = minute.set_index("open_time_dt")
    expected_days = (end - start).days + 1
    if len(minute) < expected_days * 1_430:
        raise RuntimeError(
            f"incomplete {symbol} data: {len(minute)} rows for {expected_days} days",
        )

    rule = f"{BAR_MINUTES}min"
    bars = minute.resample(rule, label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        quote_volume=("quote_volume", "sum"),
        minute_count=("close", "count"),
    )
    bars = bars[bars["minute_count"] == BAR_MINUTES].copy()
    prior_close = bars["close"].shift(1)
    true_range = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - prior_close).abs(),
            (bars["low"] - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["atr"] = true_range.shift(1).rolling(
        PRIOR_BALANCE_BARS, min_periods=PRIOR_BALANCE_BARS,
    ).median()
    bars["prior_high"] = bars["high"].shift(1).rolling(
        PRIOR_BALANCE_BARS, min_periods=PRIOR_BALANCE_BARS,
    ).max()
    bars["prior_low"] = bars["low"].shift(1).rolling(
        PRIOR_BALANCE_BARS, min_periods=PRIOR_BALANCE_BARS,
    ).min()
    past_range = true_range.shift(1).rolling(
        PRIOR_BALANCE_BARS * 2, min_periods=PRIOR_BALANCE_BARS,
    ).median()
    past_volume = bars["quote_volume"].shift(1).rolling(
        PRIOR_BALANCE_BARS * 2, min_periods=PRIOR_BALANCE_BARS,
    ).median()
    bars["range_burst"] = (bars["high"] - bars["low"]) / past_range.replace(0.0, np.nan)
    bars["volume_burst"] = bars["quote_volume"] / past_volume.replace(0.0, np.nan)
    bar_range = (bars["high"] - bars["low"]).replace(0.0, np.nan)
    bars["close_location"] = (bars["close"] - bars["low"]) / bar_range
    bars["ready"] = bars[
        ["atr", "prior_high", "prior_low", "range_burst", "volume_burst"]
    ].notna().all(axis=1)
    return bars, evidence


def _summary(values: list[float]) -> dict[str, Any]:
    clean = pd.Series(values, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return {
            "count": 0,
            "mean_net_bps": None,
            "median_net_bps": None,
            "win_rate_after_hurdle": None,
            "p10_net_bps": None,
            "p90_net_bps": None,
            "largest_winner_share": None,
        }
    winners = clean[clean > 0.0]
    positive_sum = float(winners.sum())
    return {
        "count": int(len(clean)),
        "mean_net_bps": float(clean.mean()),
        "median_net_bps": float(clean.median()),
        "win_rate_after_hurdle": float((clean > 0.0).mean()),
        "p10_net_bps": float(clean.quantile(0.10)),
        "p90_net_bps": float(clean.quantile(0.90)),
        "largest_winner_share": (
            float(winners.max() / positive_sum) if positive_sum > 0.0 else None
        ),
    }


def _forward_net_bps(
    bars: pd.DataFrame,
    entry_index: int,
    direction: int,
    horizon_bars: int,
) -> float | None:
    exit_index = entry_index + horizon_bars - 1
    if entry_index >= len(bars) or exit_index >= len(bars):
        return None
    entry = float(bars.iloc[entry_index]["open"])
    exit_price = float(bars.iloc[exit_index]["close"])
    if entry <= 0.0 or exit_price <= 0.0:
        return None
    return direction * math.log(exit_price / entry) * 10_000.0 - ROUND_TRIP_HURDLE_BPS


def _is_parent_quality(row: pd.Series) -> bool:
    return bool(
        row["ready"]
        and row["range_burst"] >= MIN_RANGE_BURST
        and row["volume_burst"] >= MIN_VOLUME_BURST
        and row["atr"] > 0.0
    )


def _collect_symbol_events(symbol: str, bars: pd.DataFrame) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    last_parent = -10**9
    i = 0
    while i < len(bars) - max(HORIZONS_BARS) - DEFENSE_MAX_BARS - 2:
        row = bars.iloc[i]
        if i - last_parent < PARENT_COOLDOWN_BARS or not _is_parent_quality(row):
            i += 1
            continue

        atr = float(row["atr"])
        prior_high = float(row["prior_high"])
        prior_low = float(row["prior_low"])
        parent_range = float(row["high"] - row["low"])
        close_location = float(row["close_location"])
        up_acceptance = bool(
            row["close"] >= prior_high + MIN_ACCEPTANCE_BREAK_ATR * atr
            and close_location >= MIN_CLOSE_LOCATION
        )
        down_acceptance = bool(
            row["close"] <= prior_low - MIN_ACCEPTANCE_BREAK_ATR * atr
            and close_location <= 1.0 - MIN_CLOSE_LOCATION
        )
        up_failure = bool(
            row["high"] >= prior_high + MIN_SWEEP_ATR * atr
            and row["close"] < prior_high
            and close_location <= MAX_FAILED_CLOSE_LOCATION
        )
        down_failure = bool(
            row["low"] <= prior_low - MIN_SWEEP_ATR * atr
            and row["close"] > prior_low
            and close_location >= 1.0 - MAX_FAILED_CLOSE_LOCATION
        )
        if sum((up_acceptance, down_acceptance, up_failure, down_failure)) != 1:
            i += 1
            continue

        last_parent = i
        parent_ts = bars.index[i]
        common = {
            "symbol": symbol,
            "parent_timestamp": parent_ts.isoformat(),
            "parent_range_bps": parent_range / float(row["close"]) * 10_000.0,
            "prior_balance_width_bps": (
                (prior_high - prior_low) / float(row["close"]) * 10_000.0
            ),
            "range_burst": float(row["range_burst"]),
            "volume_burst": float(row["volume_burst"]),
            "close_location": close_location,
        }

        if up_acceptance or down_acceptance:
            direction = 1 if up_acceptance else -1
            boundary = prior_high if direction > 0 else prior_low
            immediate_entry = i + 1
            immediate = {
                **common,
                "family": "ACCEPTANCE_IMMEDIATE",
                "direction": direction,
                "transition_timestamp": parent_ts.isoformat(),
                "entry_timestamp": bars.index[immediate_entry].isoformat(),
                "boundary": boundary,
            }
            for horizon in HORIZONS_BARS:
                immediate[f"net_{horizon * BAR_MINUTES}m_bps"] = _forward_net_bps(
                    bars, immediate_entry, direction, horizon,
                )
            events.append(immediate)

            defense_index: int | None = None
            invalidated = False
            for j in range(i + 1, min(i + DEFENSE_MAX_BARS + 1, len(bars) - 1)):
                later = bars.iloc[j]
                if direction > 0:
                    if later["close"] < boundary - MAX_DEFENSE_INSIDE_ATR * atr:
                        invalidated = True
                        break
                    retraced = later["low"] <= (
                        float(row["close"]) - MIN_DEFENSE_RETRACE_PARENT_RANGE * parent_range
                    )
                    held = later["low"] >= boundary - MAX_DEFENSE_INSIDE_ATR * atr
                    reexpanded = later["close"] > later["open"] and later["close"] > boundary
                else:
                    if later["close"] > boundary + MAX_DEFENSE_INSIDE_ATR * atr:
                        invalidated = True
                        break
                    retraced = later["high"] >= (
                        float(row["close"]) + MIN_DEFENSE_RETRACE_PARENT_RANGE * parent_range
                    )
                    held = later["high"] <= boundary + MAX_DEFENSE_INSIDE_ATR * atr
                    reexpanded = later["close"] < later["open"] and later["close"] < boundary
                if retraced and held and reexpanded:
                    defense_index = j
                    break
            if defense_index is not None and not invalidated:
                entry_index = defense_index + 1
                defense = {
                    **common,
                    "family": "ACCEPTANCE_FIRST_DEFENSE",
                    "direction": direction,
                    "transition_timestamp": bars.index[defense_index].isoformat(),
                    "entry_timestamp": bars.index[entry_index].isoformat(),
                    "boundary": boundary,
                }
                for horizon in HORIZONS_BARS:
                    defense[f"net_{horizon * BAR_MINUTES}m_bps"] = _forward_net_bps(
                        bars, entry_index, direction, horizon,
                    )
                events.append(defense)
        else:
            direction = -1 if up_failure else 1
            entry_index = i + 1
            boundary = prior_high if up_failure else prior_low
            failure = {
                **common,
                "family": "FAILED_AUCTION_REVERSAL",
                "direction": direction,
                "transition_timestamp": parent_ts.isoformat(),
                "entry_timestamp": bars.index[entry_index].isoformat(),
                "boundary": boundary,
            }
            for horizon in HORIZONS_BARS:
                failure[f"net_{horizon * BAR_MINUTES}m_bps"] = _forward_net_bps(
                    bars, entry_index, direction, horizon,
                )
            events.append(failure)
        i += 1
    return events


def run_screen(start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    all_events: list[dict[str, Any]] = []
    raw_evidence: list[dict[str, Any]] = []
    bar_counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        bars, evidence = _load_symbol(symbol, start, end, cache)
        raw_evidence.extend(evidence)
        bar_counts[symbol] = int(len(bars))
        all_events.extend(_collect_symbol_events(symbol, bars))

    events = pd.DataFrame(all_events)
    events.to_csv(output / "auction_events.csv", index=False)
    results: dict[str, Any] = {}
    promising: list[dict[str, Any]] = []
    if not events.empty:
        for symbol in SYMBOLS:
            results[symbol] = {}
            for family in (
                "ACCEPTANCE_IMMEDIATE",
                "ACCEPTANCE_FIRST_DEFENSE",
                "FAILED_AUCTION_REVERSAL",
            ):
                subset = events[(events["symbol"] == symbol) & (events["family"] == family)]
                family_result: dict[str, Any] = {
                    "events": int(len(subset)),
                    "horizons": {},
                }
                for horizon in HORIZONS_BARS:
                    minutes = horizon * BAR_MINUTES
                    stats = _summary(subset[f"net_{minutes}m_bps"].tolist())
                    family_result["horizons"][str(minutes)] = stats
                    if (
                        stats["count"] >= 20
                        and stats["mean_net_bps"] is not None
                        and stats["mean_net_bps"] > 0.0
                        and stats["median_net_bps"] is not None
                        and stats["median_net_bps"] > 0.0
                        and stats["win_rate_after_hurdle"] is not None
                        and stats["win_rate_after_hurdle"] >= 0.55
                        and (
                            stats["largest_winner_share"] is None
                            or stats["largest_winner_share"] <= 0.35
                        )
                    ):
                        promising.append(
                            {
                                "symbol": symbol,
                                "family": family,
                                "horizon_minutes": minutes,
                                **stats,
                            },
                        )
                results[symbol][family] = family_result

    report = {
        "schema": "external-auction-acceptance-screen-v1",
        "development_period": [start.isoformat(), end.isoformat()],
        "symbols": list(SYMBOLS),
        "bar_minutes": BAR_MINUTES,
        "bar_counts": bar_counts,
        "prior_balance_bars": PRIOR_BALANCE_BARS,
        "prior_balance_minutes": PRIOR_BALANCE_BARS * BAR_MINUTES,
        "entry_timing": "strictly later 15-minute open",
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "parent_cooldown_minutes": PARENT_COOLDOWN_BARS * BAR_MINUTES,
        "fixed_parameters": {
            "min_range_burst": MIN_RANGE_BURST,
            "min_volume_burst": MIN_VOLUME_BURST,
            "min_acceptance_break_atr": MIN_ACCEPTANCE_BREAK_ATR,
            "min_sweep_atr": MIN_SWEEP_ATR,
            "min_close_location": MIN_CLOSE_LOCATION,
            "max_failed_close_location": MAX_FAILED_CLOSE_LOCATION,
            "defense_max_bars": DEFENSE_MAX_BARS,
            "max_defense_inside_atr": MAX_DEFENSE_INSIDE_ATR,
            "min_defense_retrace_parent_range": MIN_DEFENSE_RETRACE_PARENT_RANGE,
        },
        "event_count": int(len(events)),
        "event_counts": (
            events.groupby(["symbol", "family"]).size().rename("count").reset_index().to_dict("records")
            if not events.empty else []
        ),
        "results": results,
        "promising_fixed_cells": promising,
        "interpretation": (
            "A pass only shows enough fixed-horizon movement after the complete causal sequence. "
            "It is not a trading result and cannot replace NautilusTrader execution validation."
        ),
    }
    (output / "auction_screen.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "raw_evidence.json").write_text(
        json.dumps(raw_evidence, indent=2, sort_keys=True) + "\n",
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
    report = run_screen(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        args.cache,
        args.output,
    )
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
