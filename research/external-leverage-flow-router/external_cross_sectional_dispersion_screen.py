#!/usr/bin/env python3
"""Fixed market-wide shock dispersion screen for the four-symbol universe.

Unlike a BTC-always-leads model, this screen first identifies a synchronized
market shock across BTC, ETH, SOL, and XRP.  It then separates two relative
states:

* OVEREXTENSION: one market moved materially farther than its three peers;
* LAGGER: one aligned market absorbed materially less of the common shock.

The overextended market is evaluated both immediately and only after a strictly
later failed-continuation/re-entry bar.  The lagger is evaluated in the common
shock direction.  One target is selected per family and parent event.  Parent
events have a fixed two-hour cooldown, so repeated bars from the same shock are
not counted as new independent opportunities.

This is an economic-space event screen, not a backtester.  A passing family
must still be implemented in NautilusTrader with actual fills, one global
position, risk sizing, costs, and continuous account NAV.
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
HORIZONS_BARS = (1, 2, 4, 8, 16)
ROUND_TRIP_HURDLE_BPS = 20.0
PARENT_COOLDOWN_BARS = 8
MIN_ALIGNED_MARKETS = 3
MIN_MARKET_MEDIAN_ATR = 1.25
MIN_MARKET_MEDIAN_RETURN_BPS = 15.0
MIN_HIGH_VOLUME_MARKETS = 2
MIN_VOLUME_BURST = 1.50
MIN_RESIDUAL_ATR = 0.75
MIN_OVEREXTENDED_RESPONSE_ATR = 1.75
MAX_TRANSITION_BARS = 2
TRANSITION_ATTEMPT_TOLERANCE_ATR = 0.10


def _load_symbol(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    minute_frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    day = start
    while day <= end:
        archive, _checksum, raw = download_checked(
            "klines", symbol, day, cache / symbol,
        )
        minute_frames.append(robust_read_kline(archive))
        evidence.append(asdict(raw))
        day += timedelta(days=1)

    minute = pd.concat(minute_frames, ignore_index=True).sort_values("open_time_dt")
    if minute["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate minute for {symbol}")
    expected_days = (end - start).days + 1
    if len(minute) < expected_days * 1_430:
        raise RuntimeError(
            f"incomplete {symbol} data: {len(minute)} rows for {expected_days} days",
        )
    minute = minute.set_index("open_time_dt")
    bars = minute.resample(
        f"{BAR_MINUTES}min", label="left", closed="left",
    ).agg(
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
    atr_price = true_range.shift(1).rolling(32, min_periods=16).median()
    bars["atr_price"] = atr_price
    bars["atr_bps"] = atr_price / prior_close.replace(0.0, np.nan) * 10_000.0
    bars["return_bps"] = np.log(bars["close"] / bars["open"]) * 10_000.0
    bars["return_atr"] = bars["return_bps"] / bars["atr_bps"].replace(0.0, np.nan)
    past_volume = bars["quote_volume"].shift(1).rolling(32, min_periods=16).median()
    bars["volume_burst"] = bars["quote_volume"] / past_volume.replace(0.0, np.nan)
    bar_range = (bars["high"] - bars["low"]).replace(0.0, np.nan)
    bars["close_location"] = (bars["close"] - bars["low"]) / bar_range

    rename = {
        column: f"{symbol}_{column}"
        for column in (
            "open",
            "high",
            "low",
            "close",
            "atr_price",
            "atr_bps",
            "return_bps",
            "return_atr",
            "volume_burst",
            "close_location",
        )
    }
    bars = bars.rename(columns=rename)
    return bars[list(rename.values())], evidence


def _forward_path(
    frame: pd.DataFrame,
    symbol: str,
    entry_index: int,
    direction: int,
    horizon_bars: int,
) -> dict[str, float | None]:
    exit_index = entry_index + horizon_bars - 1
    if entry_index >= len(frame) or exit_index >= len(frame):
        return {"net_close_bps": None, "mfe_net_bps": None, "mae_bps": None}
    entry = float(frame.iloc[entry_index][f"{symbol}_open"])
    exit_price = float(frame.iloc[exit_index][f"{symbol}_close"])
    if entry <= 0.0 or exit_price <= 0.0:
        return {"net_close_bps": None, "mfe_net_bps": None, "mae_bps": None}
    path = frame.iloc[entry_index : exit_index + 1]
    gross_close = direction * math.log(exit_price / entry) * 10_000.0
    if direction > 0:
        favorable = math.log(float(path[f"{symbol}_high"].max()) / entry) * 10_000.0
        adverse = math.log(float(path[f"{symbol}_low"].min()) / entry) * 10_000.0
    else:
        favorable = math.log(entry / float(path[f"{symbol}_low"].min())) * 10_000.0
        adverse = math.log(entry / float(path[f"{symbol}_high"].max())) * 10_000.0
    return {
        "net_close_bps": gross_close - ROUND_TRIP_HURDLE_BPS,
        "mfe_net_bps": favorable - ROUND_TRIP_HURDLE_BPS,
        "mae_bps": adverse,
    }


def _summary(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
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


def _append_event_paths(
    event: dict[str, Any],
    frame: pd.DataFrame,
    symbol: str,
    entry_index: int,
    direction: int,
) -> None:
    for horizon in HORIZONS_BARS:
        minutes = horizon * BAR_MINUTES
        path = _forward_path(frame, symbol, entry_index, direction, horizon)
        event[f"net_{minutes}m_bps"] = path["net_close_bps"]
        event[f"mfe_net_{minutes}m_bps"] = path["mfe_net_bps"]
        event[f"mae_{minutes}m_bps"] = path["mae_bps"]


def _later_failed_continuation(
    frame: pd.DataFrame,
    parent_index: int,
    symbol: str,
    shock_direction: int,
) -> int | None:
    parent = frame.iloc[parent_index]
    parent_high = float(parent[f"{symbol}_high"])
    parent_low = float(parent[f"{symbol}_low"])
    parent_mid = (parent_high + parent_low) / 2.0
    parent_atr = float(parent[f"{symbol}_atr_price"])
    tolerance = TRANSITION_ATTEMPT_TOLERANCE_ATR * parent_atr
    for index in range(
        parent_index + 1,
        min(parent_index + MAX_TRANSITION_BARS + 1, len(frame) - 1),
    ):
        row = frame.iloc[index]
        if shock_direction > 0:
            attempted = float(row[f"{symbol}_high"]) >= parent_high - tolerance
            reentered = float(row[f"{symbol}_close"]) <= parent_mid
        else:
            attempted = float(row[f"{symbol}_low"]) <= parent_low + tolerance
            reentered = float(row[f"{symbol}_close"]) >= parent_mid
        if attempted and reentered:
            return index
    return None


def _collect_events(frame: pd.DataFrame) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    independent_parents = 0
    last_parent_index = -10**9
    max_horizon = max(HORIZONS_BARS)
    for i in range(32, len(frame) - max_horizon - MAX_TRANSITION_BARS - 2):
        if i - last_parent_index < PARENT_COOLDOWN_BARS:
            continue
        row = frame.iloc[i]
        returns_atr = np.array(
            [float(row[f"{symbol}_return_atr"]) for symbol in SYMBOLS],
            dtype=float,
        )
        returns_bps = np.array(
            [float(row[f"{symbol}_return_bps"]) for symbol in SYMBOLS],
            dtype=float,
        )
        volumes = np.array(
            [float(row[f"{symbol}_volume_burst"]) for symbol in SYMBOLS],
            dtype=float,
        )
        if not (
            np.isfinite(returns_atr).all()
            and np.isfinite(returns_bps).all()
            and np.isfinite(volumes).all()
        ):
            continue
        median_return = float(np.median(returns_atr))
        shock_direction = 1 if median_return > 0.0 else -1
        signed_atr = shock_direction * returns_atr
        signed_bps = shock_direction * returns_bps
        aligned = signed_atr > 0.0
        aligned_count = int(aligned.sum())
        market_median_atr = float(np.median(signed_atr))
        market_median_bps = float(np.median(signed_bps))
        high_volume_count = int((volumes >= MIN_VOLUME_BURST).sum())
        if not (
            aligned_count >= MIN_ALIGNED_MARKETS
            and market_median_atr >= MIN_MARKET_MEDIAN_ATR
            and market_median_bps >= MIN_MARKET_MEDIAN_RETURN_BPS
            and high_volume_count >= MIN_HIGH_VOLUME_MARKETS
        ):
            continue

        last_parent_index = i
        independent_parents += 1
        parent_timestamp = frame.index[i].isoformat()

        over_index = int(np.argmax(signed_atr))
        over_symbol = SYMBOLS[over_index]
        over_peer_median = float(np.median(np.delete(signed_atr, over_index)))
        over_residual = float(signed_atr[over_index] - over_peer_median)
        if (
            over_residual >= MIN_RESIDUAL_ATR
            and signed_atr[over_index] >= MIN_OVEREXTENDED_RESPONSE_ATR
        ):
            entry_index = i + 1
            event = {
                "parent_timestamp": parent_timestamp,
                "family": "OVEREXTENSION_IMMEDIATE_REVERSAL",
                "target_symbol": over_symbol,
                "shock_direction": shock_direction,
                "trade_direction": -shock_direction,
                "market_median_atr": market_median_atr,
                "market_median_bps": market_median_bps,
                "aligned_markets": aligned_count,
                "high_volume_markets": high_volume_count,
                "target_response_atr": float(signed_atr[over_index]),
                "target_residual_atr": over_residual,
                "target_volume_burst": float(volumes[over_index]),
                "transition_timestamp": parent_timestamp,
                "entry_timestamp": frame.index[entry_index].isoformat(),
            }
            _append_event_paths(
                event, frame, over_symbol, entry_index, -shock_direction,
            )
            events.append(event)

            transition_index = _later_failed_continuation(
                frame, i, over_symbol, shock_direction,
            )
            if transition_index is not None:
                entry_index = transition_index + 1
                confirmed = dict(event)
                confirmed["family"] = "OVEREXTENSION_REENTRY_REVERSAL"
                confirmed["transition_timestamp"] = frame.index[transition_index].isoformat()
                confirmed["entry_timestamp"] = frame.index[entry_index].isoformat()
                _append_event_paths(
                    confirmed, frame, over_symbol, entry_index, -shock_direction,
                )
                events.append(confirmed)

        aligned_indices = np.flatnonzero(aligned)
        if len(aligned_indices) >= MIN_ALIGNED_MARKETS:
            lag_index = int(aligned_indices[np.argmin(signed_atr[aligned_indices])])
            lag_symbol = SYMBOLS[lag_index]
            lag_peer_median = float(np.median(np.delete(signed_atr, lag_index)))
            lag_residual = float(lag_peer_median - signed_atr[lag_index])
            if lag_residual >= MIN_RESIDUAL_ATR:
                entry_index = i + 1
                lagger = {
                    "parent_timestamp": parent_timestamp,
                    "family": "LAGGER_CATCH_UP",
                    "target_symbol": lag_symbol,
                    "shock_direction": shock_direction,
                    "trade_direction": shock_direction,
                    "market_median_atr": market_median_atr,
                    "market_median_bps": market_median_bps,
                    "aligned_markets": aligned_count,
                    "high_volume_markets": high_volume_count,
                    "target_response_atr": float(signed_atr[lag_index]),
                    "target_residual_atr": lag_residual,
                    "target_volume_burst": float(volumes[lag_index]),
                    "transition_timestamp": parent_timestamp,
                    "entry_timestamp": frame.index[entry_index].isoformat(),
                }
                _append_event_paths(
                    lagger, frame, lag_symbol, entry_index, shock_direction,
                )
                events.append(lagger)
    return events, independent_parents


def run_screen(start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    joined: pd.DataFrame | None = None
    evidence: list[dict[str, Any]] = []
    bar_counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        bars, raw = _load_symbol(symbol, start, end, cache)
        evidence.extend(raw)
        bar_counts[symbol] = int(len(bars))
        joined = bars if joined is None else joined.join(bars, how="inner")
    assert joined is not None
    frame = joined.sort_index()
    expected_bars = ((end - start).days + 1) * 96
    if len(frame) < expected_bars * 0.97:
        raise RuntimeError("cross-sectional one-to-one 15-minute join lost too many rows")

    event_rows, independent_parents = _collect_events(frame)
    events = pd.DataFrame(event_rows)
    events.to_csv(output / "dispersion_events.csv", index=False)

    families = (
        "OVEREXTENSION_IMMEDIATE_REVERSAL",
        "OVEREXTENSION_REENTRY_REVERSAL",
        "LAGGER_CATCH_UP",
    )
    results: dict[str, Any] = {}
    promising: list[dict[str, Any]] = []
    for family in families:
        subset = events[events["family"] == family] if not events.empty else pd.DataFrame()
        family_result: dict[str, Any] = {
            "events": int(len(subset)),
            "target_symbol_counts": (
                subset["target_symbol"].value_counts().sort_index().to_dict()
                if not subset.empty else {}
            ),
            "horizons": {},
            "by_target_symbol": {},
        }
        for horizon in HORIZONS_BARS:
            minutes = horizon * BAR_MINUTES
            stats = _summary(
                subset[f"net_{minutes}m_bps"] if not subset.empty
                else pd.Series(dtype=float),
            )
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
                    {"family": family, "horizon_minutes": minutes, **stats},
                )
        if not subset.empty:
            for symbol in SYMBOLS:
                target_subset = subset[subset["target_symbol"] == symbol]
                family_result["by_target_symbol"][symbol] = {
                    str(horizon * BAR_MINUTES): _summary(
                        target_subset[f"net_{horizon * BAR_MINUTES}m_bps"],
                    )
                    for horizon in HORIZONS_BARS
                }
        results[family] = family_result

    report = {
        "schema": "external-cross-sectional-dispersion-screen-v1",
        "development_period": [start.isoformat(), end.isoformat()],
        "symbols": list(SYMBOLS),
        "bar_minutes": BAR_MINUTES,
        "bar_counts": bar_counts,
        "joined_bars": int(len(frame)),
        "entry_timing": "strictly later 15-minute open",
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "parent_cooldown_minutes": PARENT_COOLDOWN_BARS * BAR_MINUTES,
        "independent_parent_events": independent_parents,
        "fixed_parameters": {
            "min_aligned_markets": MIN_ALIGNED_MARKETS,
            "min_market_median_atr": MIN_MARKET_MEDIAN_ATR,
            "min_market_median_return_bps": MIN_MARKET_MEDIAN_RETURN_BPS,
            "min_high_volume_markets": MIN_HIGH_VOLUME_MARKETS,
            "min_volume_burst": MIN_VOLUME_BURST,
            "min_residual_atr": MIN_RESIDUAL_ATR,
            "min_overextended_response_atr": MIN_OVEREXTENDED_RESPONSE_ATR,
            "max_transition_bars": MAX_TRANSITION_BARS,
        },
        "event_count": int(len(events)),
        "event_counts": (
            events.groupby(["family", "target_symbol"]).size().rename("count").reset_index().to_dict("records")
            if not events.empty else []
        ),
        "results": results,
        "promising_fixed_cells": promising,
        "interpretation": (
            "A pass identifies economic space after a complete cross-sectional state sequence. "
            "It is not a trading result and cannot bypass NautilusTrader validation."
        ),
    }
    (output / "dispersion_screen.json").write_text(
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
    report = run_screen(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        args.cache,
        args.output,
    )
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
