#!/usr/bin/env python3
"""Harvest causal skilled-trader plans whose only exits are take-profit or stop-loss.

The script builds event-conditioned plans directly from completed price/volume history.
It never liquidates because a clock expired.  If neither predeclared barrier is reached
before the available label data ends, the plan remains CENSORED_OPEN with no realized R.

OB/FVG, trend/channel, sweep/trap and volume are not independent voting strategies here.
They describe location, market state, control transfer and route geometry for one action.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RE1 = ROOT / "research" / "candidate-easychart_re1"
if str(RE1) not in sys.path:
    sys.path.insert(0, str(RE1))

from data_re1_flow import load_range_flow  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TICKS = {"BTCUSDT": 0.1, "ETHUSDT": 0.01, "SOLUSDT": 0.001, "XRPUSDT": 0.0001}
ENTRY_FEE = 0.0005
STOP_FEE = 0.0005
TARGET_FEE = 0.0002
ENTRY_SLIPPAGE_TICKS = 2
STOP_SLIPPAGE_TICKS = 2
EPS = 1e-12


@dataclass(frozen=True, slots=True)
class BarrierResult:
    outcome: str
    net_r: float | None
    exit_time: str
    hold_minutes: int
    actual_entry: float
    actual_stop_net_r: float
    actual_target_net_r: float
    mfe_r: float
    mae_r: float


def _derive_raw(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy().sort_values("open_time_dt")
    quote = out["quote_volume"].astype(float).clip(lower=EPS)
    out["signed_quote_flow"] = 2.0 * out["taker_buy_quote_volume"].astype(float) - quote
    out["taker_imbalance"] = (out["signed_quote_flow"] / quote).clip(-1.0, 1.0)
    prior = out["close"].shift(1)
    out["true_range"] = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prior).abs(),
            (out["low"] - prior).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out.index = pd.DatetimeIndex(out.pop("open_time_dt"))
    return out


def _resample5(frame: pd.DataFrame) -> pd.DataFrame:
    bars = frame.resample("5min", label="right", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "quote_volume": "sum",
            "signed_quote_flow": "sum",
            "taker_buy_quote_volume": "sum",
            "count": "sum",
        }
    ).dropna(subset=["open", "high", "low", "close"])
    bars["imbalance"] = bars["signed_quote_flow"] / bars["quote_volume"].clip(lower=EPS)
    previous = bars["close"].shift(1)
    bars["tr"] = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous).abs(),
            (bars["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["atr20"] = bars["tr"].rolling(20, min_periods=10).median()
    bars["ret1"] = np.log(bars["close"].clip(lower=EPS)).diff()
    for window in (3, 6, 12, 24, 48, 72):
        minimum = max(3, window // 2)
        bars[f"ret{window}"] = np.log(bars["close"].clip(lower=EPS)).diff(window)
        bars[f"range{window}"] = (
            bars["high"].rolling(window, min_periods=minimum).max()
            - bars["low"].rolling(window, min_periods=minimum).min()
        )
        bars[f"vol{window}"] = bars["ret1"].rolling(window, min_periods=minimum).std()
        bars[f"flow{window}"] = (
            bars["signed_quote_flow"].rolling(window, min_periods=minimum).sum()
            / bars["quote_volume"].rolling(window, min_periods=minimum).sum().clip(lower=EPS)
        )
        travelled = bars["close"].diff().abs().rolling(window, min_periods=minimum).sum()
        bars[f"eff{window}"] = bars["close"].diff(window).abs() / travelled.clip(lower=EPS)
        weighted = (bars["close"] * bars["quote_volume"]).rolling(window, min_periods=minimum).sum()
        bars[f"vwap{window}"] = weighted / bars["quote_volume"].rolling(window, min_periods=minimum).sum().clip(lower=EPS)
        bars[f"vwapdist{window}"] = (bars["close"] - bars[f"vwap{window}"]) / bars["atr20"].clip(lower=EPS)
    for window in (12, 24, 72, 288):
        high = bars["high"].rolling(window, min_periods=max(4, window // 2)).max().shift(1)
        low = bars["low"].rolling(window, min_periods=max(4, window // 2)).min().shift(1)
        bars[f"loc{window}"] = (bars["close"] - low) / (high - low).clip(lower=EPS)
        bars[f"above{window}"] = (bars["close"] - high) / bars["atr20"].clip(lower=EPS)
        bars[f"below{window}"] = (low - bars["close"]) / bars["atr20"].clip(lower=EPS)
    volume_median = bars["quote_volume"].rolling(72, min_periods=20).median()
    volume_mad = (bars["quote_volume"] - volume_median).abs().rolling(72, min_periods=20).median()
    bars["activity_z"] = (bars["quote_volume"] - volume_median) / (1.4826 * volume_mad).clip(lower=EPS)
    bars["compression"] = bars["range12"] / bars["range72"].clip(lower=EPS)
    bars["vol_compression"] = bars["vol12"] / bars["vol48"].clip(lower=EPS)
    bars["body_atr"] = (bars["close"] - bars["open"]) / bars["atr20"].clip(lower=EPS)
    bars["close_location"] = (bars["close"] - bars["low"]) / (bars["high"] - bars["low"]).clip(lower=EPS)

    high24 = bars["high"].rolling(24, min_periods=12).max().shift(1)
    low24 = bars["low"].rolling(24, min_periods=12).min().shift(1)
    bars["sweep_high"] = ((bars["high"] > high24) & (bars["close"] < high24)).astype(float)
    bars["sweep_low"] = ((bars["low"] < low24) & (bars["close"] > low24)).astype(float)
    bars["break_high"] = ((bars["close"] > high24) & (bars["body_atr"] > 0.0)).astype(float)
    bars["break_low"] = ((bars["close"] < low24) & (bars["body_atr"] < 0.0)).astype(float)
    price_response = bars["close"].diff() / bars["atr20"].clip(lower=EPS)
    bars["absorption_long"] = (
        (-bars["imbalance"]).clip(lower=0.0)
        * (-price_response).clip(lower=0.0)
        / (price_response.abs() + 0.15)
    ).clip(0.0, 5.0)
    bars["absorption_short"] = (
        bars["imbalance"].clip(lower=0.0)
        * price_response.clip(lower=0.0)
        / (price_response.abs() + 0.15)
    ).clip(0.0, 5.0)
    return bars.replace([np.inf, -np.inf], np.nan)


def _add_common_state(bars: dict[str, pd.DataFrame]) -> None:
    common_index: pd.DatetimeIndex | None = None
    for frame in bars.values():
        common_index = frame.index if common_index is None else common_index.intersection(frame.index)
    if common_index is None or len(common_index) == 0:
        return
    returns = pd.DataFrame(index=common_index)
    for symbol, frame in bars.items():
        returns[symbol] = frame.reindex(common_index)["ret1"]
    factor = returns.mean(axis=1)
    breadth = np.sign(returns).mean(axis=1)
    dispersion = returns.std(axis=1)
    common = pd.DataFrame(
        {"factor_ret1": factor, "breadth1": breadth, "dispersion1": dispersion},
        index=common_index,
    )
    for window in (3, 6, 12, 24):
        minimum = max(2, window // 2)
        common[f"factor_ret{window}"] = factor.rolling(window, min_periods=minimum).sum()
        common[f"breadth{window}"] = breadth.rolling(window, min_periods=minimum).mean()
        common[f"dispersion{window}"] = dispersion.rolling(window, min_periods=minimum).mean()
    for symbol in list(bars):
        frame = bars[symbol].join(common, how="left")
        for window in (3, 6, 12, 24):
            frame[f"relative_ret{window}"] = frame[f"ret{window}"] - frame[f"factor_ret{window}"]
        bars[symbol] = frame
    for window in (6, 12):
        return_matrix = pd.DataFrame({s: bars[s].reindex(common_index)[f"ret{window}"] for s in bars})
        flow_matrix = pd.DataFrame({s: bars[s].reindex(common_index)[f"flow{window}"] for s in bars})
        return_rank = return_matrix.rank(axis=1, pct=True)
        flow_rank = flow_matrix.rank(axis=1, pct=True)
        for symbol in bars:
            bars[symbol][f"rank_ret{window}"] = return_rank[symbol].reindex(bars[symbol].index)
            bars[symbol][f"rank_flow{window}"] = flow_rank[symbol].reindex(bars[symbol].index)


def _prior_atr(frame: pd.DataFrame, index: int, window: int = 120) -> float:
    start = max(1, index - window)
    part = frame.iloc[start:index]
    previous = frame["close"].shift(1).iloc[start:index]
    tr = pd.concat(
        [part["high"] - part["low"], (part["high"] - previous).abs(), (part["low"] - previous).abs()],
        axis=1,
    ).max(axis=1)
    return float(tr.median())


def _event_family(row: pd.Series) -> str:
    if float(row.get("sweep_low", 0.0)) > 0.0 or float(row.get("sweep_high", 0.0)) > 0.0:
        return "FAILED_AUCTION"
    if max(float(row.get("absorption_long", 0.0)), float(row.get("absorption_short", 0.0))) > 0.45:
        return "BOUNDARY_ABSORPTION"
    if float(row.get("break_high", 0.0)) > 0.0 or float(row.get("break_low", 0.0)) > 0.0:
        return "ACCEPTED_OR_INITIATIVE_BREAK"
    if float(row.get("vol_compression", 1.0)) < 0.55 and abs(float(row.get("body_atr", 0.0))) > 0.35:
        return "COMPRESSION_RELEASE"
    if abs(float(row.get("flow3", 0.0)) - float(row.get("flow12", 0.0))) > 0.35:
        return "FLOW_CONTROL_TRANSFER"
    if abs(float(row.get("relative_ret6", 0.0))) > 0.7 * (abs(float(row.get("factor_ret6", 0.0))) + 1e-4):
        return "RELATIVE_STRENGTH"
    if abs(float(row.get("ret12", 0.0))) > 0.0 and abs(float(row.get("ret3", 0.0))) < 0.45 * abs(float(row.get("ret12", 0.0))) and float(row.get("eff24", 0.0)) > 0.35:
        return "TREND_PULLBACK"
    return "STATE_TRANSITION"


def _event_positions(bars: pd.DataFrame, decision_start: pd.Timestamp, decision_end: pd.Timestamp) -> Iterable[int]:
    change = (bars["ret3"].abs() / bars["vol24"].clip(lower=1e-6)).clip(0.0, 8.0) * 0.12
    change += ((bars["flow3"] - bars["flow12"]).abs() * 1.2).clip(0.0, 3.0)
    change += bars[["sweep_high", "sweep_low", "break_high", "break_low"]].max(axis=1) * 0.9
    change += (1.0 - bars["vol_compression"]).clip(lower=0.0) * bars["body_atr"].abs().clip(0.0, 3.0) * 0.5
    change += bars["relative_ret6"].abs().clip(0.0, 3.0) * 0.3
    threshold = change.rolling(288, min_periods=60).quantile(0.68)
    event = (change > threshold) & (change > 0.22) & bars["atr20"].notna()
    ordinal = np.arange(len(bars))
    event |= (
        (ordinal % 6 == 0)
        & ((bars["eff24"] > 0.38) | (bars["loc72"] < 0.12) | (bars["loc72"] > 0.88))
        & bars["atr20"].notna()
    )
    event &= bars.index >= decision_start
    event &= bars.index < decision_end
    last = -10_000
    for position in np.flatnonzero(event.to_numpy()):
        if int(position) - last <= 2:
            continue
        last = int(position)
        yield int(position)


def _dedupe_routes(candidates: list[tuple[float, float, str]], tick: float) -> list[tuple[float, float, str]]:
    output: list[tuple[float, float, str]] = []
    for item in sorted(candidates, key=lambda value: (value[0], value[2])):
        if any(abs(item[1] - prior[1]) <= 3.0 * tick for prior in output):
            continue
        output.append(item)
    return output


def _route_candidates(frame: pd.DataFrame, index: int, entry: float, side: str, tick: float) -> list[tuple[float, float, str]]:
    sign = 1.0 if side == "LONG" else -1.0
    candidates: list[tuple[float, float, str]] = []
    for horizon in (30, 60, 120, 240, 480, 1440):
        history = frame.iloc[max(0, index - horizon):index]
        if len(history) < max(12, horizon // 4):
            continue
        price = float(history["high"].max()) if side == "LONG" else float(history["low"].min())
        distance = sign * (price - entry)
        if distance > 0.0:
            candidates.append((distance, price, f"PRIOR_{'HIGH' if side == 'LONG' else 'LOW'}_{horizon}"))
    day = frame.index[index].normalize()
    previous_day = frame[(frame.index >= day - pd.Timedelta(days=1)) & (frame.index < day)]
    if len(previous_day) >= 120:
        price = float(previous_day["high"].max()) if side == "LONG" else float(previous_day["low"].min())
        distance = sign * (price - entry)
        if distance > 0.0:
            candidates.append((distance, price, "PDH" if side == "LONG" else "PDL"))
    profile = frame.iloc[max(0, index - 1440):index]
    if len(profile) >= 180 and float(profile["quote_volume"].sum()) > 0.0:
        typical = ((profile["high"] + profile["low"] + profile["close"]) / 3.0).to_numpy(float)
        weights = profile["quote_volume"].to_numpy(float)
        lower, upper = np.quantile(typical, [0.01, 0.99])
        if math.isfinite(lower) and math.isfinite(upper) and upper > lower:
            edges = np.linspace(lower, upper, 65)
            volume, _ = np.histogram(typical, bins=edges, weights=weights)
            positive = volume[volume > 0.0]
            if len(positive) >= 8:
                cutoff = np.quantile(positive, 0.75)
                for bin_index, value in enumerate(volume):
                    left = volume[bin_index - 1] if bin_index else -np.inf
                    right = volume[bin_index + 1] if bin_index + 1 < len(volume) else -np.inf
                    if value < cutoff or value < left or value < right:
                        continue
                    price = float(edges[bin_index]) if side == "LONG" else float(edges[bin_index + 1])
                    distance = sign * (price - entry)
                    if distance > 0.0:
                        candidates.append((distance, price, "CAUSAL_24H_VOLUME_NODE"))
    return _dedupe_routes(candidates, tick)


def _barrier_result(
    frame: pd.DataFrame,
    entry_index: int,
    side: str,
    stop: float,
    target: float,
    tick: float,
) -> BarrierResult | None:
    sign = 1.0 if side == "LONG" else -1.0
    actual_entry = float(frame["open"].iloc[entry_index]) + sign * ENTRY_SLIPPAGE_TICKS * tick
    stop_fill = float(stop) - sign * STOP_SLIPPAGE_TICKS * tick
    valid = stop_fill < actual_entry < target if side == "LONG" else target < actual_entry < stop_fill
    if not valid:
        return None
    risk = abs(actual_entry - stop_fill)
    raw_stop = sign * (stop_fill - actual_entry) / risk - (
        ENTRY_FEE * abs(actual_entry) + STOP_FEE * abs(stop_fill)
    ) / risk
    normalization = max(abs(raw_stop), EPS)
    raw_target = sign * (target - actual_entry) / risk - (
        ENTRY_FEE * abs(actual_entry) + TARGET_FEE * abs(target)
    ) / risk
    target_r = raw_target / normalization
    if target_r <= 0.0:
        return None

    high = frame["high"].to_numpy(dtype=float, copy=False)
    low = frame["low"].to_numpy(dtype=float, copy=False)
    if side == "LONG":
        stop_hits = low[entry_index:] <= stop
        target_hits = high[entry_index:] >= target
    else:
        stop_hits = high[entry_index:] >= stop
        target_hits = low[entry_index:] <= target
    first_stop = int(np.argmax(stop_hits)) if bool(stop_hits.any()) else None
    first_target = int(np.argmax(target_hits)) if bool(target_hits.any()) else None

    if first_stop is None and first_target is None:
        end = len(frame) - 1
        if side == "LONG":
            mfe = (float(np.max(high[entry_index:end + 1])) - actual_entry) / risk / normalization
            mae = (float(np.min(low[entry_index:end + 1])) - actual_entry) / risk / normalization
        else:
            mfe = (actual_entry - float(np.min(low[entry_index:end + 1]))) / risk / normalization
            mae = (actual_entry - float(np.max(high[entry_index:end + 1]))) / risk / normalization
        return BarrierResult(
            outcome="CENSORED_OPEN",
            net_r=None,
            exit_time=str(frame.index[end] + pd.Timedelta(minutes=1)),
            hold_minutes=end - entry_index + 1,
            actual_entry=actual_entry,
            actual_stop_net_r=-1.0,
            actual_target_net_r=target_r,
            mfe_r=mfe,
            mae_r=mae,
        )

    # One-minute OHLC cannot order two barriers printed in the same bar.  Assign stop.
    if first_stop is not None and (first_target is None or first_stop <= first_target):
        relative = first_stop
        outcome = "STOP_FIRST"
        net_r = -1.0
    else:
        relative = int(first_target)
        outcome = "TARGET_FIRST"
        net_r = target_r
    end = entry_index + int(relative)
    if side == "LONG":
        mfe = (float(np.max(high[entry_index:end + 1])) - actual_entry) / risk / normalization
        mae = (float(np.min(low[entry_index:end + 1])) - actual_entry) / risk / normalization
    else:
        mfe = (actual_entry - float(np.min(low[entry_index:end + 1]))) / risk / normalization
        mae = (actual_entry - float(np.max(high[entry_index:end + 1]))) / risk / normalization
    return BarrierResult(
        outcome=outcome,
        net_r=net_r,
        exit_time=str(frame.index[end] + pd.Timedelta(minutes=1)),
        hold_minutes=end - entry_index + 1,
        actual_entry=actual_entry,
        actual_stop_net_r=-1.0,
        actual_target_net_r=target_r,
        mfe_r=mfe,
        mae_r=mae,
    )


FEATURE_COLUMNS = [
    "ret1", "ret3", "ret6", "ret12", "ret24", "ret48",
    "range3", "range6", "range12", "range24", "range48", "range72",
    "vol3", "vol6", "vol12", "vol24", "vol48",
    "flow3", "flow6", "flow12", "flow24", "flow48",
    "eff6", "eff12", "eff24", "eff48",
    "vwapdist12", "vwapdist24", "vwapdist72",
    "loc12", "loc24", "loc72", "loc288",
    "above12", "above24", "above72", "below12", "below24", "below72",
    "activity_z", "compression", "vol_compression", "body_atr", "close_location",
    "sweep_high", "sweep_low", "break_high", "break_low",
    "absorption_long", "absorption_short",
    "factor_ret1", "factor_ret3", "factor_ret6", "factor_ret12", "factor_ret24",
    "breadth1", "breadth3", "breadth6", "breadth12", "breadth24",
    "dispersion1", "dispersion3", "dispersion6", "dispersion12", "dispersion24",
    "relative_ret3", "relative_ret6", "relative_ret12", "relative_ret24",
    "rank_ret6", "rank_ret12", "rank_flow6", "rank_flow12",
]


def harvest_period(
    *,
    period: str,
    start: date,
    end: date,
    warmup_days: int,
    label_days: int,
    cache: Path,
    output: Path,
) -> dict[str, object]:
    decision_start = pd.Timestamp(start, tz="UTC")
    decision_end = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    load_start = start - timedelta(days=warmup_days)
    load_end = end + timedelta(days=label_days)
    raw: dict[str, pd.DataFrame] = {}
    bars: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        raw[symbol] = _derive_raw(load_range_flow(symbol, load_start, load_end, cache))
        bars[symbol] = _resample5(raw[symbol])
    _add_common_state(bars)

    records: list[dict[str, object]] = []
    by_symbol: dict[str, dict[str, int]] = {}
    for symbol in SYMBOLS:
        frame = raw[symbol]
        state = bars[symbol]
        tick = TICKS[symbol]
        event_count = 0
        plan_count = 0
        for event_number, position in enumerate(_event_positions(state, decision_start, decision_end), start=1):
            event_time = state.index[position]
            entry_index = int(frame.index.searchsorted(event_time, side="left"))
            if entry_index < 180 or entry_index >= len(frame) - 1:
                continue
            atr = _prior_atr(frame, entry_index)
            if not math.isfinite(atr) or atr <= 4.0 * tick:
                continue
            row = state.iloc[position]
            family = _event_family(row)
            event_id = f"LAV4:{period}:{symbol}:{event_number}:{int(event_time.value)}"
            features = {
                column: float(row[column]) if column in row and pd.notna(row[column]) else 0.0
                for column in FEATURE_COLUMNS
            }
            features["hour_sin"] = math.sin(2.0 * math.pi * event_time.hour / 24.0)
            features["hour_cos"] = math.cos(2.0 * math.pi * event_time.hour / 24.0)
            features["weekday_sin"] = math.sin(2.0 * math.pi * event_time.weekday() / 7.0)
            features["weekday_cos"] = math.cos(2.0 * math.pi * event_time.weekday() / 7.0)
            event_count += 1

            history15 = frame.iloc[max(0, entry_index - 15):entry_index]
            history45 = frame.iloc[max(0, entry_index - 45):entry_index]
            noise = max(2.0 * tick, 0.12 * float(frame["true_range"].iloc[max(0, entry_index - 120):entry_index].median()))
            for side in ("LONG", "SHORT"):
                sign = 1.0 if side == "LONG" else -1.0
                provisional_entry = float(frame["open"].iloc[entry_index]) + sign * ENTRY_SLIPPAGE_TICKS * tick
                stops = [
                    (
                        "LOCAL_SWING_15",
                        float(history15["low"].min()) - noise if side == "LONG" else float(history15["high"].max()) + noise,
                    ),
                    (
                        "STRUCTURE_SWING_45",
                        float(history45["low"].min()) - noise if side == "LONG" else float(history45["high"].max()) + noise,
                    ),
                ]
                routes = _route_candidates(frame, entry_index, provisional_entry, side, tick)
                for stop_kind, stop in stops:
                    if not (stop < provisional_entry if side == "LONG" else stop > provisional_entry):
                        continue
                    risk = abs(provisional_entry - stop)
                    risk_bps = risk / max(abs(provisional_entry), EPS) * 10_000.0
                    if risk_bps < 2.0 or risk_bps > 350.0:
                        continue
                    eligible = [route for route in routes if 1.0 <= route[0] / risk <= 3.0][:3]
                    for route_distance, target, route_kind in eligible:
                        result = _barrier_result(frame, entry_index, side, stop, target, tick)
                        if result is None:
                            continue
                        identity = hashlib.sha1(
                            f"{event_id}|{side}|{stop_kind}|{target:.12g}|{route_kind}".encode("utf-8")
                        ).hexdigest()[:18]
                        record = {
                            "period": period,
                            "symbol": symbol,
                            "event_id": event_id,
                            "action_id": f"LAV4A:{identity}",
                            "family": family,
                            "side": side,
                            "event_time": str(event_time),
                            "entry_time": str(frame.index[entry_index]),
                            "entry": provisional_entry,
                            "stop": stop,
                            "target": target,
                            "stop_kind": stop_kind,
                            "route_kind": route_kind,
                            "risk_bps": risk_bps,
                            "gross_rr": route_distance / risk,
                            **features,
                            **asdict(result),
                        }
                        records.append(record)
                        plan_count += 1
        by_symbol[symbol] = {"events": event_count, "plans": plan_count}

    frame = pd.DataFrame(records)
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / "barrier_actions.csv.gz", index=False, compression="gzip")
    summary = {
        "period": period,
        "decision_start": start.isoformat(),
        "decision_end": end.isoformat(),
        "label_data_end": load_end.isoformat(),
        "actions": int(len(frame)),
        "events": int(frame["event_id"].nunique()) if not frame.empty else 0,
        "outcomes": frame["outcome"].value_counts().to_dict() if not frame.empty else {},
        "by_symbol": by_symbol,
        "exit_contract": "TAKE_PROFIT_OR_STOP_LOSS_ONLY; unresolved plans are right-censored open positions",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=3)
    parser.add_argument("--label-days", type=int, default=14)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = harvest_period(
        period=args.period,
        start=args.start,
        end=args.end,
        warmup_days=args.warmup_days,
        label_days=args.label_days,
        cache=args.cache,
        output=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
