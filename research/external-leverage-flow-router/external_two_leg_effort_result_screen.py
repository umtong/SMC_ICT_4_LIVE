#!/usr/bin/env python3
"""Fixed two-leg spot/perpetual effort-result exhaustion screen.

The externally mined decision sequence is deliberately more complete than a
single high-volume candle or oscillator divergence:

    first price-discovery leg at a completed four-hour extreme
    -> controlled pullback which leaves the first leg origin intact
    -> second price extreme with weaker spot effort and greater perpetual share
    -> strictly later failed-continuation transition
    -> entry, invalidation, objective from the new reversal leg

Two transitions are diagnosed separately:

* REENTRY: price closes back inside the first-leg extreme; the first pullback
  extreme is the objective.
* PIVOT_BREAK: price breaks the pullback extreme; the first-leg origin is the
  objective.

All signals use completed 15-minute spot and perpetual bars.  Entry is always a
strictly later open.  Geometry must clear a fixed 20 bp round-trip hurdle and a
net reward/risk of at least 1.0 before the event is admitted.  Same-bar stop and
target ambiguity is resolved against the strategy.  This remains an event and
geometry screen; a passing family still requires NautilusTrader validation.
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
from spot_participation_contract import _download_spot_checked


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BAR_MINUTES = 15
PRIOR_EXTREME_BARS = 16
MAX_PULLBACK_BARS = 4
MAX_SECOND_PUSH_BARS = 4
MAX_REENTRY_BARS = 2
MAX_PIVOT_BREAK_BARS = 4
MAX_HOLD_BARS = 16
PARENT_COOLDOWN_BARS = 16
ROUND_TRIP_HURDLE_BPS = 20.0
MIN_NET_RR = 1.0
MIN_LEG1_RANGE_ATR = 1.50
MIN_LEG1_SPOT_VOLUME_BURST = 1.50
MIN_LEG1_PERP_VOLUME_BURST = 1.50
MIN_LEG1_CLOSE_LOCATION = 0.70
MIN_PULLBACK_RETRACE = 0.20
MAX_PULLBACK_RETRACE = 0.75
ORIGIN_TOLERANCE_ATR = 0.10
MIN_SECOND_EXTENSION_ATR = 0.05
MAX_SECOND_CLOSE_EXTENSION_LEG1 = 0.50
MAX_SECOND_SPOT_EFFORT_RATIO = 0.85
MIN_PERP_SHARE_EXPANSION = 1.10
MIN_TRANSITION_SPOT_EFFORT_RATIO = 0.80
STOP_BUFFER_ATR = 0.05


def _resample_15m(minute: pd.DataFrame) -> pd.DataFrame:
    minute = minute.sort_values("open_time_dt").set_index("open_time_dt")
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
    return bars[bars["minute_count"] == BAR_MINUTES].copy()


def _load_symbol(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    perp_frames: list[pd.DataFrame] = []
    spot_frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    day = start
    while day <= end:
        perp_path, _perp_checksum, perp_raw = download_checked(
            "klines", symbol, day, cache / symbol / "perpetual",
        )
        spot_path, _spot_checksum, spot_raw = _download_spot_checked(
            "klines", symbol, day, cache / symbol,
        )
        perp_frames.append(robust_read_kline(perp_path))
        spot_frames.append(robust_read_kline(spot_path))
        evidence.extend([asdict(perp_raw), asdict(spot_raw)])
        day += timedelta(days=1)

    expected_days = (end - start).days + 1
    perp_minute = pd.concat(perp_frames, ignore_index=True)
    spot_minute = pd.concat(spot_frames, ignore_index=True)
    for label, minute in (("perpetual", perp_minute), ("spot", spot_minute)):
        if minute["open_time_dt"].duplicated().any():
            raise RuntimeError(f"duplicate {label} minute for {symbol}")
        if len(minute) < expected_days * 1_430:
            raise RuntimeError(
                f"incomplete {label} {symbol}: {len(minute)} rows for {expected_days} days",
            )

    perp = _resample_15m(perp_minute)
    spot = _resample_15m(spot_minute)
    frame = perp.join(spot, how="inner", lsuffix="_perp", rsuffix="_spot")
    if len(frame) < expected_days * 96 * 0.97:
        raise RuntimeError(f"perpetual/spot join lost too many bars for {symbol}")

    prior_close = frame["close_perp"].shift(1)
    true_range = pd.concat(
        [
            frame["high_perp"] - frame["low_perp"],
            (frame["high_perp"] - prior_close).abs(),
            (frame["low_perp"] - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr_price"] = true_range.shift(1).rolling(32, min_periods=32).median()
    frame["prior_high"] = frame["high_perp"].shift(1).rolling(
        PRIOR_EXTREME_BARS, min_periods=PRIOR_EXTREME_BARS,
    ).max()
    frame["prior_low"] = frame["low_perp"].shift(1).rolling(
        PRIOR_EXTREME_BARS, min_periods=PRIOR_EXTREME_BARS,
    ).min()
    for venue in ("perp", "spot"):
        past_volume = frame[f"quote_volume_{venue}"].shift(1).rolling(
            32, min_periods=32,
        ).median()
        frame[f"volume_burst_{venue}"] = (
            frame[f"quote_volume_{venue}"] / past_volume.replace(0.0, np.nan)
        )
        bar_range = (
            frame[f"high_{venue}"] - frame[f"low_{venue}"]
        ).replace(0.0, np.nan)
        frame[f"close_location_{venue}"] = (
            frame[f"close_{venue}"] - frame[f"low_{venue}"]
        ) / bar_range
    frame["perp_spot_volume_ratio"] = (
        frame["quote_volume_perp"] / frame["quote_volume_spot"].replace(0.0, np.nan)
    )
    frame["ready"] = frame[
        [
            "atr_price",
            "prior_high",
            "prior_low",
            "volume_burst_perp",
            "volume_burst_spot",
            "close_location_perp",
            "close_location_spot",
            "perp_spot_volume_ratio",
        ]
    ].notna().all(axis=1)
    return frame, evidence


def _leg1_direction(row: pd.Series) -> int:
    atr = float(row["atr_price"])
    bar_range = float(row["high_perp"] - row["low_perp"])
    common = bool(
        row["ready"]
        and bar_range >= MIN_LEG1_RANGE_ATR * atr
        and row["volume_burst_spot"] >= MIN_LEG1_SPOT_VOLUME_BURST
        and row["volume_burst_perp"] >= MIN_LEG1_PERP_VOLUME_BURST
    )
    if not common:
        return 0
    long_leg = bool(
        row["high_perp"] > row["prior_high"]
        and row["close_perp"] > row["prior_high"]
        and row["close_location_perp"] >= MIN_LEG1_CLOSE_LOCATION
        and row["close_spot"] > row["open_spot"]
        and row["close_location_spot"] >= MIN_LEG1_CLOSE_LOCATION
    )
    short_leg = bool(
        row["low_perp"] < row["prior_low"]
        and row["close_perp"] < row["prior_low"]
        and row["close_location_perp"] <= 1.0 - MIN_LEG1_CLOSE_LOCATION
        and row["close_spot"] < row["open_spot"]
        and row["close_location_spot"] <= 1.0 - MIN_LEG1_CLOSE_LOCATION
    )
    if long_leg == short_leg:
        return 0
    return 1 if long_leg else -1


def _find_pullback(
    frame: pd.DataFrame,
    leg1_index: int,
    direction: int,
    atr: float,
) -> int | None:
    leg1 = frame.iloc[leg1_index]
    leg1_range = float(leg1["high_perp"] - leg1["low_perp"])
    origin = float(leg1["open_perp"])
    best_index: int | None = None
    best_retrace = -math.inf
    for index in range(
        leg1_index + 1,
        min(leg1_index + MAX_PULLBACK_BARS + 1, len(frame)),
    ):
        row = frame.iloc[index]
        if direction > 0:
            retrace = (float(leg1["high_perp"]) - float(row["low_perp"])) / leg1_range
            origin_intact = float(row["low_perp"]) >= origin - ORIGIN_TOLERANCE_ATR * atr
        else:
            retrace = (float(row["high_perp"]) - float(leg1["low_perp"])) / leg1_range
            origin_intact = float(row["high_perp"]) <= origin + ORIGIN_TOLERANCE_ATR * atr
        if not origin_intact or retrace > MAX_PULLBACK_RETRACE:
            return None
        if MIN_PULLBACK_RETRACE <= retrace <= MAX_PULLBACK_RETRACE and retrace > best_retrace:
            best_retrace = retrace
            best_index = index
    return best_index


def _find_second_push(
    frame: pd.DataFrame,
    leg1_index: int,
    pullback_index: int,
    direction: int,
    atr: float,
) -> int | None:
    leg1 = frame.iloc[leg1_index]
    leg1_range = float(leg1["high_perp"] - leg1["low_perp"])
    leg1_spot_effort = float(leg1["quote_volume_spot"])
    leg1_share = float(leg1["perp_spot_volume_ratio"])
    for index in range(
        pullback_index + 1,
        min(pullback_index + MAX_SECOND_PUSH_BARS + 1, len(frame)),
    ):
        row = frame.iloc[index]
        if not bool(row["ready"]):
            continue
        if direction > 0:
            extension = float(row["high_perp"] - leg1["high_perp"])
            close_extension = max(0.0, float(row["close_perp"] - leg1["high_perp"]))
            directional_spot = float(row["close_spot"]) >= float(row["open_spot"])
        else:
            extension = float(leg1["low_perp"] - row["low_perp"])
            close_extension = max(0.0, float(leg1["low_perp"] - row["close_perp"]))
            directional_spot = float(row["close_spot"]) <= float(row["open_spot"])
        weaker_spot_effort = bool(
            float(row["quote_volume_spot"])
            <= MAX_SECOND_SPOT_EFFORT_RATIO * leg1_spot_effort
        )
        greater_perp_share = bool(
            float(row["perp_spot_volume_ratio"])
            >= MIN_PERP_SHARE_EXPANSION * leg1_share
        )
        limited_close_result = close_extension <= MAX_SECOND_CLOSE_EXTENSION_LEG1 * leg1_range
        if (
            extension >= MIN_SECOND_EXTENSION_ATR * atr
            and weaker_spot_effort
            and greater_perp_share
            and limited_close_result
            and directional_spot
        ):
            return index
    return None


def _find_transition(
    frame: pd.DataFrame,
    leg1_index: int,
    pullback_index: int,
    second_index: int,
    direction: int,
    family: str,
) -> int | None:
    leg1 = frame.iloc[leg1_index]
    second = frame.iloc[second_index]
    max_bars = MAX_REENTRY_BARS if family == "REENTRY" else MAX_PIVOT_BREAK_BARS
    for index in range(second_index + 1, min(second_index + max_bars + 1, len(frame))):
        row = frame.iloc[index]
        opposing_spot = (
            float(row["close_spot"]) < float(row["open_spot"])
            if direction > 0
            else float(row["close_spot"]) > float(row["open_spot"])
        )
        sufficient_effort = bool(
            float(row["quote_volume_spot"])
            >= MIN_TRANSITION_SPOT_EFFORT_RATIO * float(second["quote_volume_spot"])
        )
        if family == "REENTRY":
            price_transition = (
                float(row["close_perp"]) < float(leg1["high_perp"])
                if direction > 0
                else float(row["close_perp"]) > float(leg1["low_perp"])
            )
        else:
            pullback = frame.iloc[pullback_index]
            price_transition = (
                float(row["close_perp"]) < float(pullback["low_perp"])
                if direction > 0
                else float(row["close_perp"]) > float(pullback["high_perp"])
            )
        if opposing_spot and sufficient_effort and price_transition:
            return index
    return None


def _geometry(
    frame: pd.DataFrame,
    leg1_index: int,
    pullback_index: int,
    second_index: int,
    transition_index: int,
    direction: int,
    family: str,
    atr: float,
) -> dict[str, float] | None:
    entry_index = transition_index + 1
    if entry_index >= len(frame):
        return None
    entry = float(frame.iloc[entry_index]["open_perp"])
    leg1 = frame.iloc[leg1_index]
    pullback = frame.iloc[pullback_index]
    second = frame.iloc[second_index]
    reversal_direction = -direction
    if reversal_direction < 0:
        stop = float(second["high_perp"]) + STOP_BUFFER_ATR * atr
        target = (
            float(pullback["low_perp"])
            if family == "REENTRY"
            else float(leg1["open_perp"])
        )
        if not (stop > entry > target > 0.0):
            return None
        gross_reward = math.log(entry / target) * 10_000.0
        gross_risk = math.log(stop / entry) * 10_000.0
    else:
        stop = float(second["low_perp"]) - STOP_BUFFER_ATR * atr
        target = (
            float(pullback["high_perp"])
            if family == "REENTRY"
            else float(leg1["open_perp"])
        )
        if not (0.0 < stop < entry < target):
            return None
        gross_reward = math.log(target / entry) * 10_000.0
        gross_risk = math.log(entry / stop) * 10_000.0
    net_reward = gross_reward - ROUND_TRIP_HURDLE_BPS
    planned_loss = gross_risk + ROUND_TRIP_HURDLE_BPS
    if net_reward <= 0.0 or planned_loss <= 0.0:
        return None
    net_rr = net_reward / planned_loss
    if net_rr < MIN_NET_RR:
        return None
    return {
        "entry_index": float(entry_index),
        "entry": entry,
        "stop": stop,
        "target": target,
        "gross_reward_bps": gross_reward,
        "gross_risk_bps": gross_risk,
        "net_reward_bps": net_reward,
        "planned_loss_bps": planned_loss,
        "net_rr": net_rr,
    }


def _simulate(
    frame: pd.DataFrame,
    reversal_direction: int,
    geometry: dict[str, float],
) -> dict[str, Any]:
    entry_index = int(geometry["entry_index"])
    entry = geometry["entry"]
    stop = geometry["stop"]
    target = geometry["target"]
    last_index = min(entry_index + MAX_HOLD_BARS - 1, len(frame) - 1)
    exit_index = last_index
    exit_price = float(frame.iloc[last_index]["close_perp"])
    exit_reason = "TIME"
    for index in range(entry_index, last_index + 1):
        row = frame.iloc[index]
        if reversal_direction > 0:
            stop_hit = float(row["low_perp"]) <= stop
            target_hit = float(row["high_perp"]) >= target
        else:
            stop_hit = float(row["high_perp"]) >= stop
            target_hit = float(row["low_perp"]) <= target
        if stop_hit:
            exit_index = index
            exit_price = stop
            exit_reason = "STOP"
            break
        if target_hit:
            exit_index = index
            exit_price = target
            exit_reason = "TARGET"
            break
    gross = reversal_direction * math.log(exit_price / entry) * 10_000.0
    return {
        "exit_timestamp": frame.index[exit_index].isoformat(),
        "exit_reason": exit_reason,
        "holding_bars": int(exit_index - entry_index + 1),
        "net_pnl_bps": gross - ROUND_TRIP_HURDLE_BPS,
    }


def _summary(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return {
            "count": 0,
            "mean_net_bps": None,
            "median_net_bps": None,
            "win_rate": None,
            "profit_factor": None,
            "largest_winner_share": None,
            "p10_net_bps": None,
            "p90_net_bps": None,
        }
    wins = clean[clean > 0.0]
    losses = clean[clean < 0.0]
    positive_sum = float(wins.sum())
    negative_sum = float(-losses.sum())
    return {
        "count": int(len(clean)),
        "mean_net_bps": float(clean.mean()),
        "median_net_bps": float(clean.median()),
        "win_rate": float((clean > 0.0).mean()),
        "profit_factor": positive_sum / negative_sum if negative_sum > 0.0 else None,
        "largest_winner_share": (
            float(wins.max() / positive_sum) if positive_sum > 0.0 else None
        ),
        "p10_net_bps": float(clean.quantile(0.10)),
        "p90_net_bps": float(clean.quantile(0.90)),
    }


def _collect_symbol_events(
    symbol: str,
    frame: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events: list[dict[str, Any]] = []
    diagnostics = {
        "leg1_candidates": 0,
        "valid_pullbacks": 0,
        "second_pushes": 0,
        "reentry_transitions": 0,
        "pivot_break_transitions": 0,
        "reentry_geometry": 0,
        "pivot_break_geometry": 0,
    }
    last_parent = -10**9
    max_future = MAX_PULLBACK_BARS + MAX_SECOND_PUSH_BARS + MAX_PIVOT_BREAK_BARS + MAX_HOLD_BARS + 2
    for leg1_index in range(32, len(frame) - max_future):
        if leg1_index - last_parent < PARENT_COOLDOWN_BARS:
            continue
        leg1 = frame.iloc[leg1_index]
        direction = _leg1_direction(leg1)
        if direction == 0:
            continue
        diagnostics["leg1_candidates"] += 1
        atr = float(leg1["atr_price"])
        pullback_index = _find_pullback(frame, leg1_index, direction, atr)
        if pullback_index is None:
            continue
        diagnostics["valid_pullbacks"] += 1
        second_index = _find_second_push(
            frame, leg1_index, pullback_index, direction, atr,
        )
        if second_index is None:
            continue
        diagnostics["second_pushes"] += 1
        last_parent = leg1_index

        leg1_range = float(leg1["high_perp"] - leg1["low_perp"])
        second = frame.iloc[second_index]
        common = {
            "symbol": symbol,
            "leg1_timestamp": frame.index[leg1_index].isoformat(),
            "pullback_timestamp": frame.index[pullback_index].isoformat(),
            "second_push_timestamp": frame.index[second_index].isoformat(),
            "original_direction": direction,
            "trade_direction": -direction,
            "leg1_range_atr": leg1_range / atr,
            "leg1_spot_volume_burst": float(leg1["volume_burst_spot"]),
            "leg1_perp_volume_burst": float(leg1["volume_burst_perp"]),
            "second_spot_effort_ratio": (
                float(second["quote_volume_spot"]) / float(leg1["quote_volume_spot"])
            ),
            "perp_share_expansion": (
                float(second["perp_spot_volume_ratio"])
                / float(leg1["perp_spot_volume_ratio"])
            ),
        }

        for transition_name, family in (
            ("REENTRY", "TWO_LEG_REENTRY"),
            ("PIVOT_BREAK", "TWO_LEG_PIVOT_BREAK"),
        ):
            transition_index = _find_transition(
                frame,
                leg1_index,
                pullback_index,
                second_index,
                direction,
                transition_name,
            )
            if transition_index is None:
                continue
            diagnostics[
                "reentry_transitions" if transition_name == "REENTRY" else "pivot_break_transitions"
            ] += 1
            geometry = _geometry(
                frame,
                leg1_index,
                pullback_index,
                second_index,
                transition_index,
                direction,
                transition_name,
                atr,
            )
            if geometry is None:
                continue
            diagnostics[
                "reentry_geometry" if transition_name == "REENTRY" else "pivot_break_geometry"
            ] += 1
            entry_index = int(geometry["entry_index"])
            event = {
                **common,
                "family": family,
                "transition_timestamp": frame.index[transition_index].isoformat(),
                "entry_timestamp": frame.index[entry_index].isoformat(),
                **{k: v for k, v in geometry.items() if k != "entry_index"},
            }
            event.update(_simulate(frame, -direction, geometry))
            events.append(event)
    return events, diagnostics


def run_screen(start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    all_events: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    bar_counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        frame, raw = _load_symbol(symbol, start, end, cache)
        evidence.extend(raw)
        bar_counts[symbol] = int(len(frame))
        symbol_events, symbol_diagnostics = _collect_symbol_events(symbol, frame)
        all_events.extend(symbol_events)
        diagnostics[symbol] = symbol_diagnostics

    events = pd.DataFrame(all_events)
    events.to_csv(output / "two_leg_events.csv", index=False)
    families = ("TWO_LEG_REENTRY", "TWO_LEG_PIVOT_BREAK")
    results: dict[str, Any] = {}
    promising: list[dict[str, Any]] = []
    for family in families:
        subset = events[events["family"] == family] if not events.empty else pd.DataFrame()
        overall = _summary(
            subset["net_pnl_bps"] if not subset.empty else pd.Series(dtype=float),
        )
        results[family] = {
            "overall": overall,
            "target_rate": (
                float((subset["exit_reason"] == "TARGET").mean()) if not subset.empty else None
            ),
            "stop_rate": (
                float((subset["exit_reason"] == "STOP").mean()) if not subset.empty else None
            ),
            "symbol_counts": (
                subset["symbol"].value_counts().sort_index().to_dict()
                if not subset.empty else {}
            ),
            "by_symbol": {
                symbol: _summary(subset.loc[subset["symbol"] == symbol, "net_pnl_bps"])
                for symbol in SYMBOLS
            } if not subset.empty else {
                symbol: _summary(pd.Series(dtype=float)) for symbol in SYMBOLS
            },
        }
        if (
            overall["count"] >= 20
            and overall["mean_net_bps"] is not None
            and overall["mean_net_bps"] > 0.0
            and overall["median_net_bps"] is not None
            and overall["median_net_bps"] > 0.0
            and overall["win_rate"] is not None
            and overall["win_rate"] >= 0.55
            and overall["profit_factor"] is not None
            and overall["profit_factor"] >= 1.20
            and (
                overall["largest_winner_share"] is None
                or overall["largest_winner_share"] <= 0.35
            )
        ):
            promising.append({"family": family, **overall})

    report = {
        "schema": "external-two-leg-effort-result-screen-v1",
        "development_period": [start.isoformat(), end.isoformat()],
        "symbols": list(SYMBOLS),
        "bar_minutes": BAR_MINUTES,
        "bar_counts": bar_counts,
        "entry_timing": "strictly later 15-minute open after completed transition",
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "max_holding_minutes": MAX_HOLD_BARS * BAR_MINUTES,
        "parent_cooldown_minutes": PARENT_COOLDOWN_BARS * BAR_MINUTES,
        "fixed_parameters": {
            "prior_extreme_bars": PRIOR_EXTREME_BARS,
            "min_leg1_range_atr": MIN_LEG1_RANGE_ATR,
            "min_leg1_spot_volume_burst": MIN_LEG1_SPOT_VOLUME_BURST,
            "min_leg1_perp_volume_burst": MIN_LEG1_PERP_VOLUME_BURST,
            "min_pullback_retrace": MIN_PULLBACK_RETRACE,
            "max_pullback_retrace": MAX_PULLBACK_RETRACE,
            "min_second_extension_atr": MIN_SECOND_EXTENSION_ATR,
            "max_second_spot_effort_ratio": MAX_SECOND_SPOT_EFFORT_RATIO,
            "min_perp_share_expansion": MIN_PERP_SHARE_EXPANSION,
            "min_transition_spot_effort_ratio": MIN_TRANSITION_SPOT_EFFORT_RATIO,
            "min_net_rr": MIN_NET_RR,
        },
        "diagnostics": diagnostics,
        "event_count": int(len(events)),
        "event_counts": (
            events.groupby(["family", "symbol"]).size().rename("count").reset_index().to_dict("records")
            if not events.empty else []
        ),
        "results": results,
        "promising_fixed_families": promising,
        "interpretation": (
            "A pass indicates economic space after a complete two-leg effort/result transition. "
            "It is not a NautilusTrader result."
        ),
    }
    (output / "two_leg_screen.json").write_text(
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
