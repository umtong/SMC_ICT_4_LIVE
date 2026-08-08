#!/usr/bin/env python3
"""Fixed funding-window spot/perpetual auction screen.

The screen uses actual Binance funding timestamps and premium-index archives.
It does not trade a clock effect by itself.  Each scheduled event is routed into
one of two causal states built only from completed data before the event:

* LEVERAGE_PRESSURE_RELEASE: the pre-funding move follows the funding-paying
  side, premium expands in that direction, perpetual participation grows
  relative to spot, and spot under-participates.  A strictly later post-funding
  bar must reverse through the pre-window midpoint while spot flow reverses and
  premium contracts.
* SPOT_SPONSORED_CONTINUATION: spot confirms most of the pre-funding move and
  supplies abnormal volume while premium and perpetual share do not expand.
  A strictly later post-funding bar must hold beyond the pre-window extreme with
  continuing spot flow.

Entry occurs at the next 15-minute open after the completed transition.  Stop,
target, and net reward/risk are known before entry.  A fixed 20 bp round-trip
hurdle is charged and same-bar stop/target ambiguity is resolved against the
strategy.  Because positions last at most four hours and begin after funding,
no second funding timestamp can occur while a screened position is open.

This is an event/geometry screen, not a replacement for NautilusTrader.  Any
passing family must be implemented with actual fills, one global position,
current-NAV 3% risk sizing, and continuous account NAV.
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
from spot_participation_contract import _download_spot_checked
from vision_derivatives_contract import (
    download_monthly_checked,
    months_between,
    read_full_kline,
    read_funding_rate,
    read_premium_index_kline,
)


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BAR_MINUTES = 15
PRE_BARS = 4
BASELINE_BARS = 16
MAX_HOLD_BARS = 16
ROUND_TRIP_HURDLE_BPS = 20.0
MIN_NET_RR = 1.0
MIN_PRE_MOVE_BPS = 15.0
MIN_PRE_MOVE_ATR = 0.75
MIN_FUNDING_ABS_BPS = 0.25
MIN_PREMIUM_EXPANSION_BPS = 0.25
MIN_PREMIUM_CONTRACTION_BPS = 0.10
MIN_PERP_SHARE_EXPANSION = 1.10
MAX_CROWDED_SPOT_RETURN_RATIO = 0.90
MIN_FLOW_ALIGNMENT = 0.05
MIN_SPOT_RETURN_RATIO = 0.85
MIN_SPOT_VOLUME_BURST = 1.20
MAX_SPONSORED_PREMIUM_EXPANSION_BPS = 0.25
MAX_SPONSORED_PERP_SHARE_EXPANSION = 1.10
MAX_POST_PREMIUM_EXPANSION_BPS = 0.50
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
        taker_buy_quote_volume=("taker_buy_quote_volume", "sum"),
        minute_count=("close", "count"),
    )
    bars = bars[bars["minute_count"] == BAR_MINUTES].copy()
    bars["signed_taker_quote"] = (
        2.0 * bars["taker_buy_quote_volume"] - bars["quote_volume"]
    )
    return bars


def _load_symbol(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    load_start = start - timedelta(days=2)
    perp_frames: list[pd.DataFrame] = []
    spot_frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    day = load_start
    while day <= end:
        perp_path, _checksum, perp_raw = download_checked(
            "klines", symbol, day, cache / symbol / "perpetual",
        )
        spot_path, _spot_checksum, spot_raw = _download_spot_checked(
            "klines", symbol, day, cache / symbol,
        )
        perp_frames.append(read_full_kline(perp_path))
        spot_frames.append(read_full_kline(spot_path))
        evidence.extend([asdict(perp_raw), asdict(spot_raw)])
        day += timedelta(days=1)

    perp_minute = pd.concat(perp_frames, ignore_index=True)
    spot_minute = pd.concat(spot_frames, ignore_index=True)
    for label, minute in (("perpetual", perp_minute), ("spot", spot_minute)):
        if minute["open_time_dt"].duplicated().any():
            raise RuntimeError(f"duplicate {label} minute for {symbol}")

    perp = _resample_15m(perp_minute)
    spot = _resample_15m(spot_minute)

    premium_frames: list[pd.DataFrame] = []
    funding_frames: list[pd.DataFrame] = []
    for month in months_between(load_start, end):
        premium_path, premium_raw = download_monthly_checked(
            "premiumIndexKlines",
            symbol,
            month,
            cache / symbol / "derivatives",
            interval="15m",
        )
        funding_path, funding_raw = download_monthly_checked(
            "fundingRate",
            symbol,
            month,
            cache / symbol / "derivatives",
        )
        premium_frames.append(read_premium_index_kline(premium_path))
        funding_frames.append(read_funding_rate(funding_path))
        evidence.extend([premium_raw.to_dict(), funding_raw.to_dict()])

    premium = pd.concat(premium_frames, ignore_index=True).sort_values("open_time_dt")
    premium = premium.drop_duplicates("open_time_dt", keep="last").set_index("open_time_dt")
    premium = premium.rename(
        columns={
            "open": "premium_open",
            "high": "premium_high",
            "low": "premium_low",
            "close": "premium_close",
        },
    )
    funding = pd.concat(funding_frames, ignore_index=True).sort_values("calc_time_dt")
    funding = funding.drop_duplicates("calc_time_dt", keep="last")
    funding = funding[
        (funding["calc_time_dt"] >= pd.Timestamp(start, tz="UTC"))
        & (funding["calc_time_dt"] < pd.Timestamp(end + timedelta(days=1), tz="UTC"))
    ].copy()

    frame = perp.join(spot, how="inner", lsuffix="_perp", rsuffix="_spot")
    frame = frame.join(
        premium[["premium_open", "premium_high", "premium_low", "premium_close"]],
        how="inner",
    )
    frame = frame.sort_index()
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
    frame["atr_bps"] = frame["atr_price"] / prior_close.replace(0.0, np.nan) * 10_000.0
    return frame, funding, evidence


def _aggregate_flow(window: pd.DataFrame, venue: str) -> float:
    denominator = float(window[f"quote_volume_{venue}"].sum())
    if denominator <= 0.0:
        return math.nan
    return float(window[f"signed_taker_quote_{venue}"].sum()) / denominator


def _window_return_bps(window: pd.DataFrame, venue: str) -> float:
    start = float(window.iloc[0][f"open_{venue}"])
    end = float(window.iloc[-1][f"close_{venue}"])
    return math.log(end / start) * 10_000.0


def _geometry(
    frame: pd.DataFrame,
    event_index: int,
    family: str,
    original_direction: int,
    atr: float,
) -> dict[str, float] | None:
    entry_index = event_index + 1
    if entry_index >= len(frame):
        return None
    pre = frame.iloc[event_index - PRE_BARS : event_index]
    transition = frame.iloc[event_index]
    entry = float(frame.iloc[entry_index]["open_perp"])
    pre_open = float(pre.iloc[0]["open_perp"])
    pre_high = float(pre["high_perp"].max())
    pre_low = float(pre["low_perp"].min())
    pre_range = pre_high - pre_low

    if family == "LEVERAGE_PRESSURE_RELEASE":
        direction = -original_direction
        if direction > 0:
            stop = min(pre_low, float(transition["low_perp"])) - STOP_BUFFER_ATR * atr
            target = pre_open
            if not (0.0 < stop < entry < target):
                return None
            gross_reward = math.log(target / entry) * 10_000.0
            gross_risk = math.log(entry / stop) * 10_000.0
        else:
            stop = max(pre_high, float(transition["high_perp"])) + STOP_BUFFER_ATR * atr
            target = pre_open
            if not (stop > entry > target > 0.0):
                return None
            gross_reward = math.log(entry / target) * 10_000.0
            gross_risk = math.log(stop / entry) * 10_000.0
    else:
        direction = original_direction
        if direction > 0:
            stop = float(transition["low_perp"]) - STOP_BUFFER_ATR * atr
            target = pre_high + pre_range
            if not (0.0 < stop < entry < target):
                return None
            gross_reward = math.log(target / entry) * 10_000.0
            gross_risk = math.log(entry / stop) * 10_000.0
        else:
            stop = float(transition["high_perp"]) + STOP_BUFFER_ATR * atr
            target = pre_low - pre_range
            if not (stop > entry > target > 0.0):
                return None
            gross_reward = math.log(entry / target) * 10_000.0
            gross_risk = math.log(stop / entry) * 10_000.0

    net_reward = gross_reward - ROUND_TRIP_HURDLE_BPS
    planned_loss = gross_risk + ROUND_TRIP_HURDLE_BPS
    if net_reward <= 0.0 or planned_loss <= 0.0:
        return None
    net_rr = net_reward / planned_loss
    if net_rr < MIN_NET_RR:
        return None
    return {
        "entry_index": float(entry_index),
        "trade_direction": float(direction),
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
    geometry: dict[str, float],
) -> dict[str, Any]:
    entry_index = int(geometry["entry_index"])
    direction = int(geometry["trade_direction"])
    entry = geometry["entry"]
    stop = geometry["stop"]
    target = geometry["target"]
    last_index = min(entry_index + MAX_HOLD_BARS - 1, len(frame) - 1)
    exit_index = last_index
    exit_price = float(frame.iloc[last_index]["close_perp"])
    exit_reason = "TIME"
    for index in range(entry_index, last_index + 1):
        row = frame.iloc[index]
        if direction > 0:
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
    gross = direction * math.log(exit_price / entry) * 10_000.0
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
    funding: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events: list[dict[str, Any]] = []
    diagnostics = {
        "funding_events": 0,
        "aligned_events": 0,
        "pre_move_events": 0,
        "release_states": 0,
        "release_transitions": 0,
        "release_geometry": 0,
        "continuation_states": 0,
        "continuation_transitions": 0,
        "continuation_geometry": 0,
    }
    index_locations = {timestamp: i for i, timestamp in enumerate(frame.index)}
    for funding_row in funding.itertuples(index=False):
        event_time = funding_row.calc_time_dt
        diagnostics["funding_events"] += 1
        if event_time not in index_locations:
            continue
        event_index = index_locations[event_time]
        if event_index < PRE_BARS + BASELINE_BARS or event_index + MAX_HOLD_BARS + 2 >= len(frame):
            continue
        diagnostics["aligned_events"] += 1
        pre = frame.iloc[event_index - PRE_BARS : event_index]
        baseline = frame.iloc[
            event_index - PRE_BARS - BASELINE_BARS : event_index - PRE_BARS
        ]
        transition = frame.iloc[event_index]
        atr_bps = float(frame.iloc[event_index]["atr_bps"])
        atr = float(frame.iloc[event_index]["atr_price"])
        if not (math.isfinite(atr_bps) and atr_bps > 0.0 and math.isfinite(atr) and atr > 0.0):
            continue

        pre_return_perp = _window_return_bps(pre, "perp")
        pre_return_spot = _window_return_bps(pre, "spot")
        if pre_return_perp == 0.0:
            continue
        direction = 1 if pre_return_perp > 0.0 else -1
        if not (
            abs(pre_return_perp) >= MIN_PRE_MOVE_BPS
            and abs(pre_return_perp) >= MIN_PRE_MOVE_ATR * atr_bps
        ):
            continue
        diagnostics["pre_move_events"] += 1

        pre_flow_perp = _aggregate_flow(pre, "perp")
        pre_flow_spot = _aggregate_flow(pre, "spot")
        post_flow_spot = _aggregate_flow(frame.iloc[event_index : event_index + 1], "spot")
        post_return_perp = _window_return_bps(
            frame.iloc[event_index : event_index + 1], "perp",
        )
        post_return_spot = _window_return_bps(
            frame.iloc[event_index : event_index + 1], "spot",
        )
        if not all(
            math.isfinite(value)
            for value in (pre_flow_perp, pre_flow_spot, post_flow_spot)
        ):
            continue

        pre_perp_volume = float(pre["quote_volume_perp"].sum())
        pre_spot_volume = float(pre["quote_volume_spot"].sum())
        base_perp_volume = float(baseline["quote_volume_perp"].sum()) / BASELINE_BARS * PRE_BARS
        base_spot_volume = float(baseline["quote_volume_spot"].sum()) / BASELINE_BARS * PRE_BARS
        if min(pre_spot_volume, base_perp_volume, base_spot_volume) <= 0.0:
            continue
        pre_perp_share = pre_perp_volume / pre_spot_volume
        base_perp_share = base_perp_volume / base_spot_volume
        spot_volume_burst = pre_spot_volume / base_spot_volume
        spot_return_ratio = abs(pre_return_spot) / abs(pre_return_perp)

        premium_start_bps = float(pre.iloc[0]["premium_open"]) * 10_000.0
        premium_close_bps = float(pre.iloc[-1]["premium_close"]) * 10_000.0
        premium_post_bps = float(transition["premium_close"]) * 10_000.0
        premium_delta_bps = premium_close_bps - premium_start_bps
        premium_post_change_bps = premium_post_bps - premium_close_bps
        funding_bps = float(funding_row.last_funding_rate) * 10_000.0
        pre_high = float(pre["high_perp"].max())
        pre_low = float(pre["low_perp"].min())
        pre_mid = (pre_high + pre_low) / 2.0

        common = {
            "symbol": symbol,
            "funding_timestamp": event_time.isoformat(),
            "funding_interval_hours": float(funding_row.funding_interval_hours),
            "funding_rate_bps": funding_bps,
            "original_direction": direction,
            "pre_return_perp_bps": pre_return_perp,
            "pre_return_spot_bps": pre_return_spot,
            "pre_move_atr": abs(pre_return_perp) / atr_bps,
            "pre_flow_perp": pre_flow_perp,
            "pre_flow_spot": pre_flow_spot,
            "pre_perp_share_expansion": pre_perp_share / base_perp_share,
            "spot_volume_burst": spot_volume_burst,
            "spot_return_ratio": spot_return_ratio,
            "premium_start_bps": premium_start_bps,
            "premium_close_bps": premium_close_bps,
            "premium_delta_bps": premium_delta_bps,
            "premium_post_change_bps": premium_post_change_bps,
            "post_return_perp_bps": post_return_perp,
            "post_return_spot_bps": post_return_spot,
            "post_flow_spot": post_flow_spot,
        }

        release_state = bool(
            direction * funding_bps >= MIN_FUNDING_ABS_BPS
            and direction * premium_close_bps > 0.0
            and direction * premium_delta_bps >= MIN_PREMIUM_EXPANSION_BPS
            and pre_perp_share >= MIN_PERP_SHARE_EXPANSION * base_perp_share
            and direction * pre_flow_perp >= MIN_FLOW_ALIGNMENT
            and spot_return_ratio <= MAX_CROWDED_SPOT_RETURN_RATIO
        )
        if release_state:
            diagnostics["release_states"] += 1
            release_transition = bool(
                direction * post_return_perp < 0.0
                and direction * post_return_spot < 0.0
                and direction * post_flow_spot <= -MIN_FLOW_ALIGNMENT
                and direction * premium_post_change_bps <= -MIN_PREMIUM_CONTRACTION_BPS
                and (
                    float(transition["close_perp"]) < pre_mid
                    if direction > 0
                    else float(transition["close_perp"]) > pre_mid
                )
            )
            if release_transition:
                diagnostics["release_transitions"] += 1
                geometry = _geometry(
                    frame,
                    event_index,
                    "LEVERAGE_PRESSURE_RELEASE",
                    direction,
                    atr,
                )
                if geometry is not None:
                    diagnostics["release_geometry"] += 1
                    entry_index = int(geometry["entry_index"])
                    event = {
                        **common,
                        "family": "LEVERAGE_PRESSURE_RELEASE",
                        "transition_timestamp": frame.index[event_index].isoformat(),
                        "entry_timestamp": frame.index[entry_index].isoformat(),
                        **{
                            key: value
                            for key, value in geometry.items()
                            if key not in {"entry_index", "trade_direction"}
                        },
                        "trade_direction": int(geometry["trade_direction"]),
                    }
                    event.update(_simulate(frame, geometry))
                    events.append(event)

        sponsored_state = bool(
            direction * pre_return_spot > 0.0
            and spot_return_ratio >= MIN_SPOT_RETURN_RATIO
            and direction * pre_flow_spot >= MIN_FLOW_ALIGNMENT
            and spot_volume_burst >= MIN_SPOT_VOLUME_BURST
            and direction * premium_delta_bps <= MAX_SPONSORED_PREMIUM_EXPANSION_BPS
            and pre_perp_share <= MAX_SPONSORED_PERP_SHARE_EXPANSION * base_perp_share
        )
        if sponsored_state:
            diagnostics["continuation_states"] += 1
            continuation_transition = bool(
                direction * post_return_perp > 0.0
                and direction * post_return_spot > 0.0
                and direction * post_flow_spot >= MIN_FLOW_ALIGNMENT
                and direction * premium_post_change_bps <= MAX_POST_PREMIUM_EXPANSION_BPS
                and (
                    float(transition["close_perp"]) > pre_high
                    if direction > 0
                    else float(transition["close_perp"]) < pre_low
                )
            )
            if continuation_transition:
                diagnostics["continuation_transitions"] += 1
                geometry = _geometry(
                    frame,
                    event_index,
                    "SPOT_SPONSORED_CONTINUATION",
                    direction,
                    atr,
                )
                if geometry is not None:
                    diagnostics["continuation_geometry"] += 1
                    entry_index = int(geometry["entry_index"])
                    event = {
                        **common,
                        "family": "SPOT_SPONSORED_CONTINUATION",
                        "transition_timestamp": frame.index[event_index].isoformat(),
                        "entry_timestamp": frame.index[entry_index].isoformat(),
                        **{
                            key: value
                            for key, value in geometry.items()
                            if key not in {"entry_index", "trade_direction"}
                        },
                        "trade_direction": int(geometry["trade_direction"]),
                    }
                    event.update(_simulate(frame, geometry))
                    events.append(event)
    return events, diagnostics


def run_screen(start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    all_events: list[dict[str, Any]] = []
    raw_evidence: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    bar_counts: dict[str, int] = {}
    funding_counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        frame, funding, evidence = _load_symbol(symbol, start, end, cache)
        bar_counts[symbol] = int(len(frame))
        funding_counts[symbol] = int(len(funding))
        raw_evidence.extend(evidence)
        events, symbol_diagnostics = _collect_symbol_events(symbol, frame, funding)
        all_events.extend(events)
        diagnostics[symbol] = symbol_diagnostics

    events = pd.DataFrame(all_events)
    events.to_csv(output / "funding_auction_events.csv", index=False)
    families = ("LEVERAGE_PRESSURE_RELEASE", "SPOT_SPONSORED_CONTINUATION")
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
            overall["count"] >= 30
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
        "schema": "external-funding-auction-screen-v1",
        "development_period": [start.isoformat(), end.isoformat()],
        "symbols": list(SYMBOLS),
        "bar_minutes": BAR_MINUTES,
        "bar_counts": bar_counts,
        "funding_counts": funding_counts,
        "entry_timing": "next 15-minute open after completed post-funding transition",
        "round_trip_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
        "max_holding_minutes": MAX_HOLD_BARS * BAR_MINUTES,
        "fixed_parameters": {
            "pre_bars": PRE_BARS,
            "baseline_bars": BASELINE_BARS,
            "min_pre_move_bps": MIN_PRE_MOVE_BPS,
            "min_pre_move_atr": MIN_PRE_MOVE_ATR,
            "min_funding_abs_bps": MIN_FUNDING_ABS_BPS,
            "min_premium_expansion_bps": MIN_PREMIUM_EXPANSION_BPS,
            "min_perp_share_expansion": MIN_PERP_SHARE_EXPANSION,
            "max_crowded_spot_return_ratio": MAX_CROWDED_SPOT_RETURN_RATIO,
            "min_spot_return_ratio": MIN_SPOT_RETURN_RATIO,
            "min_spot_volume_burst": MIN_SPOT_VOLUME_BURST,
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
            "A pass indicates economic space after a complete funding-window state transition. "
            "It is not a NautilusTrader result."
        ),
    }
    (output / "funding_auction_screen.json").write_text(
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
