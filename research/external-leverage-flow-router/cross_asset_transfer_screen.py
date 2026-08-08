#!/usr/bin/env python3
"""Fixed causal event screen for BTC-to-alt intraday information transfer.

This is deliberately an event study, not a backtester.  It answers one high-
information-value question before any strategy is built: after a completed,
high-volume BTC shock, is there enough next-open-to-future-close movement in
ETH, SOL, or XRP to survive the branch's 20 bp adverse round-trip hurdle?

The externally sourced hypotheses are kept separate:

* CATCH_UP: the lagger moved in the BTC direction but by less than half of the
  BTC volatility-normalized shock, so it may continue to absorb information;
* OVERREACTION: the lagger moved at least 1.5 times as far in normalized units,
  so it may mean-revert;
* SEESAW: the lagger moved against the BTC shock, so the documented negative
  lead-lag relation may persist.

All parent features use completed minutes and shifted baselines.  Entry is the
strictly later next-minute open.  Events have a fixed 60-minute cooldown, and
thresholds are declared once in this file rather than searched.
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

from features import download_checked, read_kline


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
ALTS = SYMBOLS[1:]
HORIZONS = (1, 3, 5, 15, 30, 60)
ROUND_TRIP_HURDLE_BPS = 20.0
PARENT_MIN_RETURN_BPS = 30.0
PARENT_MIN_ATR_MULTIPLE = 4.0
PARENT_MIN_VOLUME_BURST = 5.0
PARENT_COOLDOWN_MINUTES = 60
CATCH_UP_MAX_RESPONSE_RATIO = 0.50
OVERREACTION_MIN_RESPONSE_RATIO = 1.50
SEESAW_MIN_OPPOSITE_RETURN_BPS = 10.0


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
            "klines",
            symbol,
            day,
            cache / symbol,
        )
        frames.append(read_kline(archive))
        evidence.append(asdict(raw))
        day += timedelta(days=1)
    frame = pd.concat(frames, ignore_index=True).sort_values("open_time_dt")
    if frame["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate minute for {symbol}")
    expected = (end - start).days + 1
    if len(frame) < expected * 1_430:
        raise RuntimeError(
            f"incomplete {symbol} data: {len(frame)} rows for {expected} days",
        )
    frame = frame.set_index("open_time_dt")
    prior_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prior_close).abs(),
            (frame["low"] - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Both baselines exclude the current observation.
    frame["atr_bps"] = (
        true_range.shift(1).rolling(30, min_periods=30).mean()
        / prior_close.replace(0.0, np.nan)
        * 10_000.0
    )
    frame["return_bps"] = np.log(frame["close"] / frame["open"]) * 10_000.0
    frame["return_atr"] = frame["return_bps"] / frame["atr_bps"].replace(0.0, np.nan)
    past_volume = frame["quote_volume"].shift(1).rolling(120, min_periods=60).median()
    frame["volume_burst"] = frame["quote_volume"] / past_volume.replace(0.0, np.nan)
    frame = frame.rename(
        columns={
            column: f"{symbol}_{column}"
            for column in (
                "open",
                "high",
                "low",
                "close",
                "quote_volume",
                "atr_bps",
                "return_bps",
                "return_atr",
                "volume_burst",
            )
        },
    )
    return frame[
        [
            f"{symbol}_open",
            f"{symbol}_high",
            f"{symbol}_low",
            f"{symbol}_close",
            f"{symbol}_quote_volume",
            f"{symbol}_atr_bps",
            f"{symbol}_return_bps",
            f"{symbol}_return_atr",
            f"{symbol}_volume_burst",
        ]
    ], evidence


def _independent_parent_mask(frame: pd.DataFrame) -> pd.Series:
    btc_return = frame["BTCUSDT_return_bps"]
    btc_atr = frame["BTCUSDT_atr_bps"]
    raw = (
        btc_return.abs()
        >= np.maximum(PARENT_MIN_RETURN_BPS, PARENT_MIN_ATR_MULTIPLE * btc_atr)
    ) & (frame["BTCUSDT_volume_burst"] >= PARENT_MIN_VOLUME_BURST)
    selected = pd.Series(False, index=frame.index)
    last_selected_ns = -10**30
    cooldown_ns = PARENT_COOLDOWN_MINUTES * 60 * 1_000_000_000
    for timestamp in frame.index[raw.fillna(False)]:
        ts_ns = int(timestamp.to_datetime64().astype("datetime64[ns]").astype("int64"))
        if ts_ns - last_selected_ns >= cooldown_ns:
            selected.loc[timestamp] = True
            last_selected_ns = ts_ns
    return selected


def _net_forward_bps(
    frame: pd.DataFrame,
    symbol: str,
    direction: pd.Series,
    horizon: int,
) -> pd.Series:
    # A completed parent at minute t can first enter at open[t+1].  A horizon of
    # one therefore exits at close[t+1], not at a price inside the signal bar.
    entry = frame[f"{symbol}_open"].shift(-1)
    exit_price = frame[f"{symbol}_close"].shift(-horizon)
    gross = direction * np.log(exit_price / entry) * 10_000.0
    return gross - ROUND_TRIP_HURDLE_BPS


def _summary(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
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
    positives = clean[clean > 0.0]
    positive_sum = float(positives.sum())
    largest_share = (
        float(positives.max() / positive_sum)
        if positive_sum > 0.0
        else None
    )
    return {
        "count": int(len(clean)),
        "mean_net_bps": float(clean.mean()),
        "median_net_bps": float(clean.median()),
        "win_rate_after_hurdle": float((clean > 0.0).mean()),
        "p10_net_bps": float(clean.quantile(0.10)),
        "p90_net_bps": float(clean.quantile(0.90)),
        "largest_winner_share": largest_share,
    }


def run_screen(start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    joined: pd.DataFrame | None = None
    evidence: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frame, raw = _load_symbol(symbol, start, end, cache)
        evidence.extend(raw)
        joined = frame if joined is None else joined.join(frame, how="inner")
    assert joined is not None
    frame = joined.sort_index()
    if len(frame) < ((end - start).days + 1) * 1_400:
        raise RuntimeError("cross-asset one-to-one minute join lost too many rows")

    parent = _independent_parent_mask(frame)
    btc_direction = np.sign(frame["BTCUSDT_return_bps"]).astype(float)
    parent_indices = frame.index[parent]
    event_rows: list[dict[str, Any]] = []
    results: dict[str, Any] = {}

    for symbol in ALTS:
        btc_abs_normalized = frame["BTCUSDT_return_atr"].abs()
        alt_signed_normalized = (
            btc_direction * frame[f"{symbol}_return_atr"]
        )
        alt_signed_bps = btc_direction * frame[f"{symbol}_return_bps"]
        response_ratio = alt_signed_normalized / btc_abs_normalized.replace(0.0, np.nan)

        families = {
            "CATCH_UP": parent
            & (alt_signed_bps >= -5.0)
            & (response_ratio <= CATCH_UP_MAX_RESPONSE_RATIO),
            "OVERREACTION_REVERSAL": parent
            & (response_ratio >= OVERREACTION_MIN_RESPONSE_RATIO),
            "SEESAW_CONTINUATION": parent
            & (alt_signed_bps <= -SEESAW_MIN_OPPOSITE_RETURN_BPS),
        }
        results[symbol] = {}
        for family, mask in families.items():
            trade_direction = (
                btc_direction
                if family == "CATCH_UP"
                else -btc_direction
            )
            # In a seesaw event, -BTC direction equals the observed alt direction.
            family_result: dict[str, Any] = {
                "parent_events": int(mask.fillna(False).sum()),
                "horizons": {},
            }
            for horizon in HORIZONS:
                net = _net_forward_bps(frame, symbol, trade_direction, horizon)
                family_result["horizons"][str(horizon)] = _summary(net[mask])
            results[symbol][family] = family_result

            for timestamp in frame.index[mask.fillna(False)]:
                row: dict[str, Any] = {
                    "timestamp": timestamp.isoformat(),
                    "symbol": symbol,
                    "family": family,
                    "btc_direction": int(btc_direction.loc[timestamp]),
                    "btc_return_bps": float(frame.loc[timestamp, "BTCUSDT_return_bps"]),
                    "btc_return_atr": float(frame.loc[timestamp, "BTCUSDT_return_atr"]),
                    "btc_volume_burst": float(frame.loc[timestamp, "BTCUSDT_volume_burst"]),
                    "alt_return_bps": float(frame.loc[timestamp, f"{symbol}_return_bps"]),
                    "alt_return_atr": float(frame.loc[timestamp, f"{symbol}_return_atr"]),
                    "alt_volume_burst": float(frame.loc[timestamp, f"{symbol}_volume_burst"]),
                    "response_ratio": float(response_ratio.loc[timestamp]),
                }
                for horizon in HORIZONS:
                    direction = (
                        btc_direction
                        if family == "CATCH_UP"
                        else -btc_direction
                    )
                    row[f"net_{horizon}m_bps"] = float(
                        _net_forward_bps(frame, symbol, direction, horizon).loc[timestamp],
                    )
                event_rows.append(row)

    # Require more than one event, positive mean and median after the full fixed
    # hurdle, and no worse than 60% winner concentration.  This is a screen for
    # economic space only; a passing family must still be built and validated in
    # NautilusTrader with state, stop, target, and actual fills.
    promising: list[dict[str, Any]] = []
    for symbol, symbol_result in results.items():
        for family, family_result in symbol_result.items():
            for horizon, stats in family_result["horizons"].items():
                if (
                    stats["count"] is not None
                    and stats["count"] >= 8
                    and stats["mean_net_bps"] is not None
                    and stats["mean_net_bps"] > 0.0
                    and stats["median_net_bps"] is not None
                    and stats["median_net_bps"] > 0.0
                    and stats["win_rate_after_hurdle"] is not None
                    and stats["win_rate_after_hurdle"] >= 0.55
                    and (
                        stats["largest_winner_share"] is None
                        or stats["largest_winner_share"] <= 0.60
                    )
                ):
                    promising.append(
                        {
                            "symbol": symbol,
                            "family": family,
                            "horizon_minutes": int(horizon),
                            **stats,
                        },
                    )

    events = pd.DataFrame(event_rows)
    events.to_csv(output / "cross_asset_events.csv", index=False)
    report = {
        "schema": "fixed-cross-asset-transfer-screen-v1",
        "development_period": [start.isoformat(), end.isoformat()],
        "symbols": list(SYMBOLS),
        "joined_minutes": int(len(frame)),
        "independent_btc_parent_events": int(parent.sum()),
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "entry_timing": "strictly later next-minute open",
        "parent": {
            "min_abs_return_bps": PARENT_MIN_RETURN_BPS,
            "min_atr_multiple": PARENT_MIN_ATR_MULTIPLE,
            "min_volume_burst": PARENT_MIN_VOLUME_BURST,
            "cooldown_minutes": PARENT_COOLDOWN_MINUTES,
        },
        "families": {
            "CATCH_UP": {
                "max_normalized_response_ratio": CATCH_UP_MAX_RESPONSE_RATIO,
                "trade_direction": "BTC shock direction",
            },
            "OVERREACTION_REVERSAL": {
                "min_normalized_response_ratio": OVERREACTION_MIN_RESPONSE_RATIO,
                "trade_direction": "opposite BTC shock",
            },
            "SEESAW_CONTINUATION": {
                "min_opposite_same_minute_return_bps": SEESAW_MIN_OPPOSITE_RETURN_BPS,
                "trade_direction": "opposite BTC shock",
            },
        },
        "horizons_minutes": list(HORIZONS),
        "results": results,
        "promising_fixed_cells": promising,
        "interpretation": (
            "A pass identifies economic space only. It is not a trading result "
            "and cannot bypass NautilusTrader strategy and execution validation."
        ),
    }
    (output / "cross_asset_screen.json").write_text(
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
