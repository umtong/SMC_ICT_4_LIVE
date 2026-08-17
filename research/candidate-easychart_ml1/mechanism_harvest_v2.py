#!/usr/bin/env python3
"""Harvest event/action plans from causal auction mechanisms.

The action grammar covers first interaction with completed ranges, acceptance
and first pullback, systemic forced-flow exhaustion, and common-factor residual
rejoin. Multiple timings/objectives share one causal cluster and therefore can
produce at most one account trade.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "candidate-easychart_re1"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_re1_flow import load_range_flow  # noqa: E402
from mechanism_data_v2 import (  # noqa: E402
    attach_derivatives_context,
    load_metrics_range,
    load_premium_range,
)

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TICKS = {"BTCUSDT": 0.1, "ETHUSDT": 0.01, "SOLUSDT": 0.001, "XRPUSDT": 0.0001}
TAKER_FEE = 0.00050
MAKER_FEE = 0.00020
ENTRY_SLIP_TICKS = 2
STOP_SLIP_TICKS = 2
FUNDING_CROSSING_COST = 0.00010

FAMILIES = (
    "RANGE_SWEEP_REJECTION",
    "RANGE_ACCEPTANCE_PULLBACK",
    "SYSTEMIC_FORCED_FLOW_EXHAUSTION",
    "COMMON_RESIDUAL_REJOIN",
)

FEATURE_COLUMNS = (
    "family_sweep", "family_acceptance", "family_cascade", "family_residual",
    "source_previous_8h", "source_previous_day", "source_rolling_4h",
    "source_systemic", "source_common_factor",
    "level_age_hours", "block_progress", "entry_delay_minutes",
    "planned_target_r", "risk_atr", "reward_atr", "event_range_atr",
    "event_penetration_atr", "event_close_location", "event_displacement",
    "progress_from_event_atr", "mfe_before_entry_r", "mae_before_entry_r",
    "aligned_ret_1", "aligned_ret_3", "aligned_ret_5", "aligned_ret_15",
    "aligned_ret_30", "aligned_flow_1", "aligned_flow_3", "aligned_flow_5",
    "aligned_flow_15", "aligned_flow_30", "flow_price_efficiency_3",
    "flow_price_efficiency_15", "activity_z", "trade_count_z",
    "volatility_ratio", "aligned_common_3", "aligned_common_15",
    "aligned_breadth_3", "aligned_breadth_15", "aligned_residual_z",
    "aligned_residual_change_5", "oi_change_5", "oi_change_30",
    "oi_price_agreement_5", "taker_ratio_aligned", "premium_aligned_z",
    "premium_change_aligned", "cross_asset_dispersion", "common_impulse_z",
    "absorption_3", "absorption_10", "reversal_quality", "objective_rank",
)


@dataclass(frozen=True)
class Episode:
    period: str
    episode_id: str
    cluster_id: str
    symbol: str
    family: str
    source: str
    side: int
    event_index: int
    event_time: pd.Timestamp
    level: float
    event_extreme: float
    stop: float
    targets: tuple[float, ...]
    target_sources: tuple[str, ...]
    max_hold_minutes: int
    level_age_hours: float
    event_penetration_atr: float
    event_range_atr: float


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def _rolling_z(series: pd.Series, window: int, minimum: int) -> pd.Series:
    mean = series.rolling(window, min_periods=minimum).mean()
    std = series.rolling(window, min_periods=minimum).std().replace(0.0, np.nan)
    return (series - mean) / std


def _prepare_symbol(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_values("open_time_dt").reset_index(drop=True).copy()
    out["time"] = pd.to_datetime(out["open_time_dt"], utc=True) + pd.Timedelta(minutes=1)
    close = out["close"].astype(float)
    log_close = np.log(close)
    ret = log_close.diff()
    previous = close.shift(1)
    true_range = pd.concat(
        [out["high"] - out["low"], (out["high"] - previous).abs(), (out["low"] - previous).abs()],
        axis=1,
    ).max(axis=1)
    out["atr"] = true_range.rolling(60, min_periods=20).mean()
    out["volatility_ratio"] = (
        true_range.rolling(15, min_periods=5).mean()
        / true_range.rolling(360, min_periods=120).mean().replace(0.0, np.nan)
    )
    signed_quote = 2.0 * out["taker_buy_quote_volume"] - out["quote_volume"]
    return_scale = ret.rolling(360, min_periods=120).std().replace(0.0, np.nan)
    for window in (1, 3, 5, 15, 30):
        if window == 1:
            rsum, qsum, ssum = ret, out["quote_volume"], signed_quote
        else:
            minimum = max(2, window // 3)
            rsum = ret.rolling(window, min_periods=minimum).sum()
            qsum = out["quote_volume"].rolling(window, min_periods=minimum).sum()
            ssum = signed_quote.rolling(window, min_periods=minimum).sum()
        out[f"ret_{window}"] = rsum / return_scale / math.sqrt(float(window))
        out[f"flow_{window}"] = ssum / qsum.replace(0.0, np.nan)
    out["activity_z"] = _rolling_z(np.log1p(out["quote_volume"]), 720, 180)
    out["trade_count_z"] = _rolling_z(np.log1p(out["count"]), 720, 180)
    out["close_location"] = _safe_div(out["close"] - out["low"], out["high"] - out["low"]).clip(0.0, 1.0)
    for window in (3, 15):
        net = log_close.diff(window).abs()
        path = ret.abs().rolling(window, min_periods=max(2, window // 3)).sum()
        out[f"path_eff_{window}"] = net / path.replace(0.0, np.nan)
        out[f"flow_price_eff_{window}"] = out[f"flow_{window}"].abs() * out[f"path_eff_{window}"]

    for column in (
        "open_interest", "open_interest_value", "top_account_ratio",
        "top_position_ratio", "global_account_ratio", "taker_long_short_ratio",
    ):
        if column not in out:
            out[column] = np.nan
    oi = pd.to_numeric(out["open_interest"], errors="coerce")
    out["oi_change_5"] = np.log(oi.replace(0.0, np.nan)).diff(5)
    out["oi_change_30"] = np.log(oi.replace(0.0, np.nan)).diff(30)
    taker_ratio = pd.to_numeric(out["taker_long_short_ratio"], errors="coerce")
    out["taker_ratio_log"] = np.log(taker_ratio.replace(0.0, np.nan))
    premium = pd.to_numeric(out["premium_close"], errors="coerce")
    out["premium_z"] = _rolling_z(premium, 720, 180)
    out["premium_change"] = premium.diff(5)

    out["day"] = out["time"].dt.floor("D")
    out["block"] = out["time"].dt.floor("8h")
    daily = out.groupby("day", sort=True).agg(day_high=("high", "max"), day_low=("low", "min"))
    daily["day_mid"] = 0.5 * (daily["day_high"] + daily["day_low"])
    out = out.join(daily.shift(1), on="day")
    blocks = out.groupby("block", sort=True).agg(block_high=("high", "max"), block_low=("low", "min"))
    blocks["block_mid"] = 0.5 * (blocks["block_high"] + blocks["block_low"])
    out = out.join(blocks.shift(1), on="block")
    out["block_progress"] = ((out["time"] - out["block"]) / pd.Timedelta(hours=8)).astype(float)
    out["rolling_4h_high"] = out["high"].rolling(240, min_periods=180).max().shift(1)
    out["rolling_4h_low"] = out["low"].rolling(240, min_periods=180).min().shift(1)
    out["rolling_4h_mid"] = 0.5 * (out["rolling_4h_high"] + out["rolling_4h_low"])
    quote = out["quote_volume"].rolling(60, min_periods=20).sum()
    out["fair_60"] = (
        (out["close"] * out["quote_volume"]).rolling(60, min_periods=20).sum()
        / quote.replace(0.0, np.nan)
    )
    return out


def _attach_panel(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    closes = pd.concat({symbol: frame.set_index("time")["close"] for symbol, frame in frames.items()}, axis=1).sort_index()
    returns = np.log(closes).diff()
    common = returns.median(axis=1)
    common_scale = common.rolling(720, min_periods=180).std().replace(0.0, np.nan)
    common_3 = common.rolling(3, min_periods=2).sum() / common_scale / math.sqrt(3.0)
    common_15 = common.rolling(15, min_periods=5).sum() / common_scale / math.sqrt(15.0)
    common_impulse = common.rolling(3, min_periods=2).sum()
    common_impulse_z = _rolling_z(common_impulse, 2160, 360)
    dispersion = returns.std(axis=1) / common_scale
    common_sign = np.sign(common)
    aligned = returns.apply(np.sign).eq(common_sign, axis=0).mean(axis=1)
    directional_breadth = (2.0 * aligned - 1.0) * common_sign.replace(0.0, np.nan)
    breadth_3 = directional_breadth.rolling(3, min_periods=2).mean()
    breadth_15 = directional_breadth.rolling(15, min_periods=5).mean()
    common_variance = common.rolling(720, min_periods=180).var().replace(0.0, np.nan)

    output: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        indexed = frame.set_index("time").copy()
        local = returns[symbol]
        beta = local.rolling(720, min_periods=180).cov(common) / common_variance
        residual = local - beta.clip(-3.0, 3.0) * common
        residual_sum = residual.rolling(15, min_periods=5).sum()
        residual_scale = residual_sum.rolling(2160, min_periods=360).std().replace(0.0, np.nan)
        indexed["common_3"] = common_3
        indexed["common_15"] = common_15
        indexed["common_impulse_z"] = common_impulse_z
        indexed["cross_asset_dispersion"] = dispersion
        indexed["breadth_3"] = breadth_3
        indexed["breadth_15"] = breadth_15
        indexed["residual_z"] = residual_sum / residual_scale
        indexed["residual_change_5"] = indexed["residual_z"].diff(5)
        output[symbol] = indexed.reset_index()
    return output


def _dedupe_targets(side: int, entry_hint: float, targets: Iterable[tuple[float, str]], tick: float) -> tuple[tuple[float, ...], tuple[str, ...]]:
    valid: list[tuple[float, str]] = []
    for price, source in targets:
        if not math.isfinite(float(price)) or side * (float(price) - entry_hint) <= tick:
            continue
        if any(abs(float(price) - old) <= 2.0 * tick for old, _ in valid):
            continue
        valid.append((float(price), source))
    valid.sort(key=lambda item: side * (item[0] - entry_hint))
    valid = valid[:4]
    return tuple(item[0] for item in valid), tuple(item[1] for item in valid)


def _structural_targets(row: pd.Series, side: int, entry_hint: float, family: str, level: float, tick: float) -> tuple[tuple[float, ...], tuple[str, ...]]:
    candidates: list[tuple[float, str]] = [
        (float(row.get("block_mid", np.nan)), "PREVIOUS_8H_MID"),
        (float(row.get("block_high", np.nan)), "PREVIOUS_8H_HIGH"),
        (float(row.get("block_low", np.nan)), "PREVIOUS_8H_LOW"),
        (float(row.get("day_mid", np.nan)), "PREVIOUS_DAY_MID"),
        (float(row.get("day_high", np.nan)), "PREVIOUS_DAY_HIGH"),
        (float(row.get("day_low", np.nan)), "PREVIOUS_DAY_LOW"),
        (float(row.get("rolling_4h_mid", np.nan)), "ROLLING_4H_MID"),
        (float(row.get("rolling_4h_high", np.nan)), "ROLLING_4H_HIGH"),
        (float(row.get("rolling_4h_low", np.nan)), "ROLLING_4H_LOW"),
        (float(row.get("fair_60", np.nan)), "FAIR_60"),
    ]
    block_width = float(row.get("block_high", np.nan)) - float(row.get("block_low", np.nan))
    atr = float(row.get("atr", np.nan))
    if family == "RANGE_ACCEPTANCE_PULLBACK" and math.isfinite(block_width) and block_width > tick:
        candidates.extend([
            (level + side * 0.50 * block_width, "ACCEPTANCE_HALF_RANGE"),
            (level + side * 1.00 * block_width, "ACCEPTANCE_FULL_RANGE"),
        ])
    if family == "SYSTEMIC_FORCED_FLOW_EXHAUSTION" and math.isfinite(atr):
        candidates.extend([
            (entry_hint + side * 0.80 * atr, "CASCADE_0P8_ATR"),
            (entry_hint + side * 1.40 * atr, "CASCADE_1P4_ATR"),
        ])
    return _dedupe_targets(side, entry_hint, candidates, tick)


def _range_episodes(period: str, symbol: str, frame: pd.DataFrame, start_time: pd.Timestamp, end_time: pd.Timestamp) -> list[Episode]:
    tick = TICKS[symbol]
    episodes: list[Episode] = []
    consumed: set[tuple[str, int, str]] = set()
    pending: list[dict[str, Any]] = []

    for i in range(360, len(frame) - 2):
        row = frame.iloc[i]
        timestamp = pd.Timestamp(row["time"])
        if timestamp > end_time:
            break
        atr = float(row["atr"])
        if not math.isfinite(atr) or atr <= tick:
            continue
        buffer = max(2.0 * tick, 0.035 * atr)

        survivors: list[dict[str, Any]] = []
        for state in pending:
            if i > state["expiry"]:
                continue
            side = int(state["side"])
            level = float(state["level"])
            beyond = float(row["close"]) > level + buffer if side > 0 else float(row["close"]) < level - buffer
            if not beyond:
                continue
            state["accepted"] += 1
            touched = float(row["low"]) <= level + 0.18 * atr if side > 0 else float(row["high"]) >= level - 0.18 * atr
            if state["accepted"] >= 2 and touched:
                extreme = min(float(row["low"]), level) if side > 0 else max(float(row["high"]), level)
                stop = extreme - max(2.0 * tick, 0.055 * atr) if side > 0 else extreme + max(2.0 * tick, 0.055 * atr)
                entry_hint = float(row["close"])
                targets, target_sources = _structural_targets(row, side, entry_hint, "RANGE_ACCEPTANCE_PULLBACK", level, tick)
                if targets and side * (entry_hint - stop) > tick and start_time <= timestamp <= end_time:
                    cluster = f"{period}:{symbol}:RANGE:{state['activation']}:{side}"
                    episodes.append(Episode(
                        period=period,
                        episode_id=f"{cluster}:ACCEPT:{i}",
                        cluster_id=cluster,
                        symbol=symbol,
                        family="RANGE_ACCEPTANCE_PULLBACK",
                        source=state["source"],
                        side=side,
                        event_index=i,
                        event_time=timestamp,
                        level=level,
                        event_extreme=extreme,
                        stop=stop,
                        targets=targets,
                        target_sources=target_sources,
                        max_hold_minutes=240,
                        level_age_hours=float(state["level_age_hours"]),
                        event_penetration_atr=abs(float(state["break_close"]) - level) / atr,
                        event_range_atr=(float(row["high"]) - float(row["low"])) / atr,
                    ))
                continue
            survivors.append(state)
        pending = survivors

        level_specs = [
            ("PREVIOUS_8H", "HIGH", float(row.get("block_high", np.nan)), timestamp.floor("8h") - pd.Timedelta(hours=8)),
            ("PREVIOUS_8H", "LOW", float(row.get("block_low", np.nan)), timestamp.floor("8h") - pd.Timedelta(hours=8)),
            ("PREVIOUS_DAY", "HIGH", float(row.get("day_high", np.nan)), timestamp.floor("D") - pd.Timedelta(days=1)),
            ("PREVIOUS_DAY", "LOW", float(row.get("day_low", np.nan)), timestamp.floor("D") - pd.Timedelta(days=1)),
            ("ROLLING_4H", "HIGH", float(row.get("rolling_4h_high", np.nan)), timestamp - pd.Timedelta(hours=4)),
            ("ROLLING_4H", "LOW", float(row.get("rolling_4h_low", np.nan)), timestamp - pd.Timedelta(hours=4)),
        ]
        touched_this_bar: list[tuple[float, tuple[str, str, float, pd.Timestamp]]] = []
        for spec in level_specs:
            source, kind, level, activation = spec
            if not math.isfinite(level):
                continue
            key = (source, int(round(level / tick)), str(activation.value))
            if key in consumed:
                continue
            touched = float(row["high"]) >= level if kind == "HIGH" else float(row["low"]) <= level
            if touched:
                touched_this_bar.append((abs(float(row["open"]) - level), spec))
        if not touched_this_bar:
            continue
        _, (source, kind, level, activation) = min(touched_this_bar, key=lambda item: item[0])
        key = (source, int(round(level / tick)), str(activation.value))
        consumed.add(key)
        side = -1 if kind == "HIGH" else 1
        penetration = float(row["high"]) - level if kind == "HIGH" else level - float(row["low"])
        rejected = float(row["close"]) < level - 0.01 * atr if kind == "HIGH" else float(row["close"]) > level + 0.01 * atr
        accepted = float(row["close"]) > level + buffer if kind == "HIGH" else float(row["close"]) < level - buffer
        level_age = max(0.0, (timestamp - activation) / pd.Timedelta(hours=1))
        if rejected and penetration >= max(tick, 0.02 * atr):
            extreme = float(row["high"]) if side < 0 else float(row["low"])
            stop = extreme + max(2.0 * tick, 0.055 * atr) if side < 0 else extreme - max(2.0 * tick, 0.055 * atr)
            entry_hint = float(row["close"])
            targets, target_sources = _structural_targets(row, side, entry_hint, "RANGE_SWEEP_REJECTION", level, tick)
            if targets and side * (entry_hint - stop) > tick and start_time <= timestamp <= end_time:
                cluster = f"{period}:{symbol}:RANGE:{activation.value}:{side}"
                episodes.append(Episode(
                    period=period,
                    episode_id=f"{cluster}:SWEEP:{i}",
                    cluster_id=cluster,
                    symbol=symbol,
                    family="RANGE_SWEEP_REJECTION",
                    source=source,
                    side=side,
                    event_index=i,
                    event_time=timestamp,
                    level=level,
                    event_extreme=extreme,
                    stop=stop,
                    targets=targets,
                    target_sources=target_sources,
                    max_hold_minutes=180,
                    level_age_hours=float(level_age),
                    event_penetration_atr=penetration / atr,
                    event_range_atr=(float(row["high"]) - float(row["low"])) / atr,
                ))
        elif accepted:
            pending.append({
                "side": 1 if kind == "HIGH" else -1,
                "level": level,
                "source": source,
                "activation": activation.value,
                "level_age_hours": level_age,
                "break_close": float(row["close"]),
                "accepted": 1,
                "expiry": i + 10,
            })
    return episodes


def _systemic_episodes(period: str, frames: dict[str, pd.DataFrame], start_time: pd.Timestamp, end_time: pd.Timestamp) -> list[Episode]:
    episodes: list[Episode] = []
    reference = frames["BTCUSDT"]
    last_cluster: pd.Timestamp | None = None
    for i in range(720, len(reference) - 12):
        row = reference.iloc[i]
        timestamp = pd.Timestamp(row["time"])
        if not (start_time <= timestamp <= end_time):
            continue
        impulse_z = float(row.get("common_impulse_z", np.nan))
        breadth = float(row.get("breadth_3", np.nan))
        if not math.isfinite(impulse_z) or abs(impulse_z) < 2.6 or not math.isfinite(breadth):
            continue
        impulse_side = 1 if impulse_z > 0.0 else -1
        if impulse_side * breadth < 0.35:
            continue
        if last_cluster is not None and timestamp - last_cluster < pd.Timedelta(minutes=90):
            continue
        confirmation_index: int | None = None
        for j in range(i + 1, min(i + 9, len(reference) - 2)):
            reversed_count = 0
            stalled_count = 0
            oi_contracting = 0
            for frame in frames.values():
                local = frame.iloc[j]
                aligned_flow = impulse_side * float(local.get("flow_3", np.nan))
                aligned_move = impulse_side * float(local.get("ret_3", np.nan))
                if math.isfinite(aligned_flow) and aligned_flow < 0.0:
                    reversed_count += 1
                if math.isfinite(aligned_move) and aligned_move < 0.15:
                    stalled_count += 1
                oi_change = float(local.get("oi_change_5", np.nan))
                if math.isfinite(oi_change) and oi_change < 0.0:
                    oi_contracting += 1
            if reversed_count >= 2 and stalled_count >= 3 and oi_contracting >= 2:
                confirmation_index = j
                break
        if confirmation_index is None:
            continue
        side = -impulse_side
        cluster = f"{period}:SYSTEMIC:{timestamp.value}:{side}"
        for symbol, frame in frames.items():
            event = frame.iloc[confirmation_index]
            atr = float(event.get("atr", np.nan))
            tick = TICKS[symbol]
            if not math.isfinite(atr) or atr <= tick:
                continue
            local_path = frame.iloc[max(0, i - 2):confirmation_index + 1]
            extreme = float(local_path["low"].min()) if side > 0 else float(local_path["high"].max())
            stop = extreme - max(2.0 * tick, 0.06 * atr) if side > 0 else extreme + max(2.0 * tick, 0.06 * atr)
            entry_hint = float(event["close"])
            targets, sources = _structural_targets(event, side, entry_hint, "SYSTEMIC_FORCED_FLOW_EXHAUSTION", entry_hint, tick)
            if not targets or side * (entry_hint - stop) <= tick:
                continue
            episodes.append(Episode(
                period=period,
                episode_id=f"{cluster}:{symbol}:{confirmation_index}",
                cluster_id=cluster,
                symbol=symbol,
                family="SYSTEMIC_FORCED_FLOW_EXHAUSTION",
                source="SYSTEMIC",
                side=side,
                event_index=confirmation_index,
                event_time=pd.Timestamp(event["time"]),
                level=entry_hint,
                event_extreme=extreme,
                stop=stop,
                targets=targets,
                target_sources=sources,
                max_hold_minutes=180,
                level_age_hours=0.0,
                event_penetration_atr=abs(impulse_z),
                event_range_atr=(float(local_path["high"].max()) - float(local_path["low"].min())) / atr,
            ))
        last_cluster = timestamp
    return episodes


def _residual_episodes(period: str, symbol: str, frame: pd.DataFrame, start_time: pd.Timestamp, end_time: pd.Timestamp) -> list[Episode]:
    episodes: list[Episode] = []
    last_event: pd.Timestamp | None = None
    threshold = frame["residual_z"].abs().rolling(2160, min_periods=720).quantile(0.985).shift(1)
    tick = TICKS[symbol]
    for i in range(721, len(frame) - 2):
        timestamp = pd.Timestamp(frame.iloc[i]["time"])
        if not (start_time <= timestamp <= end_time):
            continue
        if last_event is not None and timestamp - last_event < pd.Timedelta(minutes=75):
            continue
        previous_z = float(frame.iloc[i - 1]["residual_z"])
        current_z = float(frame.iloc[i]["residual_z"])
        cutoff = float(threshold.iloc[i])
        if not all(math.isfinite(value) for value in (previous_z, current_z, cutoff)):
            continue
        if abs(previous_z) < max(1.5, cutoff) or abs(current_z) >= abs(previous_z) or np.sign(current_z) != np.sign(previous_z):
            continue
        side = -1 if previous_z > 0.0 else 1
        row = frame.iloc[i]
        aligned_flow_change = side * (float(row["flow_3"]) - float(frame.iloc[i - 3]["flow_3"]))
        if not math.isfinite(aligned_flow_change) or aligned_flow_change <= 0.0:
            continue
        atr = float(row["atr"])
        if not math.isfinite(atr) or atr <= tick:
            continue
        path = frame.iloc[max(0, i - 15):i + 1]
        extreme = float(path["low"].min()) if side > 0 else float(path["high"].max())
        stop = extreme - max(2.0 * tick, 0.055 * atr) if side > 0 else extreme + max(2.0 * tick, 0.055 * atr)
        entry_hint = float(row["close"])
        targets, sources = _structural_targets(row, side, entry_hint, "COMMON_RESIDUAL_REJOIN", float(row["fair_60"]), tick)
        if not targets or side * (entry_hint - stop) <= tick:
            continue
        cluster = f"{period}:{symbol}:RESIDUAL:{timestamp.value}:{side}"
        episodes.append(Episode(
            period=period,
            episode_id=f"{cluster}:{i}",
            cluster_id=cluster,
            symbol=symbol,
            family="COMMON_RESIDUAL_REJOIN",
            source="COMMON_FACTOR",
            side=side,
            event_index=i,
            event_time=timestamp,
            level=float(row["fair_60"]),
            event_extreme=extreme,
            stop=stop,
            targets=targets,
            target_sources=sources,
            max_hold_minutes=150,
            level_age_hours=0.0,
            event_penetration_atr=abs(previous_z),
            event_range_atr=(float(path["high"].max()) - float(path["low"].min())) / atr,
        ))
        last_event = timestamp
    return episodes


def _funding_crossings(start: pd.Timestamp, end: pd.Timestamp) -> int:
    if end <= start:
        return 0
    first = start.floor("8h") + pd.Timedelta(hours=8)
    if first > end:
        return 0
    return int((end.floor("8h") - first.floor("8h")) / pd.Timedelta(hours=8)) + 1


def _label_action(frame: pd.DataFrame, entry_index: int, episode: Episode, target: float) -> dict[str, Any] | None:
    side = episode.side
    tick = TICKS[episode.symbol]
    entry_time = pd.Timestamp(frame.iloc[entry_index]["time"]) - pd.Timedelta(minutes=1)
    entry = float(frame.iloc[entry_index]["open"]) + side * ENTRY_SLIP_TICKS * tick
    stop_fill = episode.stop - side * STOP_SLIP_TICKS * tick
    gross_risk = side * (entry - stop_fill)
    gross_reward = side * (target - entry)
    if gross_risk <= tick or gross_reward <= tick:
        return None
    stop_return = side * (stop_fill - entry) / entry - 2.0 * TAKER_FEE
    target_return = side * (target - entry) / entry - TAKER_FEE - MAKER_FEE
    risk_fraction = -stop_return
    if risk_fraction <= 0.0 or target_return <= 0.0:
        return None
    planned_r = target_return / risk_fraction
    end_index = min(len(frame) - 1, entry_index + episode.max_hold_minutes)
    outcome = "TIMEOUT"
    exit_index = end_index
    for j in range(entry_index, end_index + 1):
        low = float(frame.iloc[j]["low"])
        high = float(frame.iloc[j]["high"])
        stop_hit = low <= episode.stop if side > 0 else high >= episode.stop
        target_hit = high >= target if side > 0 else low <= target
        if stop_hit:
            outcome, exit_index = "STOP_FIRST", j
            break
        if target_hit:
            outcome, exit_index = "TARGET_FIRST", j
            break
    exit_time = pd.Timestamp(frame.iloc[exit_index]["time"])
    crossings = _funding_crossings(entry_time, exit_time)
    funding_cost = FUNDING_CROSSING_COST * crossings
    if outcome == "STOP_FIRST":
        realized_return = stop_return - funding_cost
    elif outcome == "TARGET_FIRST":
        realized_return = target_return - funding_cost
    else:
        exit_price = float(frame.iloc[exit_index]["close"])
        realized_return = side * (exit_price - entry) / entry - 2.0 * TAKER_FEE - funding_cost
    return {
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "entry": entry,
        "stop": episode.stop,
        "target": target,
        "target_r": planned_r,
        "risk_fraction_of_price": risk_fraction,
        "outcome": outcome,
        "target_first": int(outcome == "TARGET_FIRST"),
        "stop_first": int(outcome == "STOP_FIRST"),
        "timeout": int(outcome == "TIMEOUT"),
        "fast_stop": int(outcome == "STOP_FIRST" and exit_index - entry_index + 1 <= 10),
        "realized_r": realized_return / risk_fraction,
        "duration_minutes": int(exit_index - entry_index + 1),
        "funding_crossings": crossings,
    }


def _features(frame: pd.DataFrame, episode: Episode, snapshot_index: int, target: float, objective_rank: int, target_r: float) -> dict[str, float]:
    side = episode.side
    event = frame.iloc[episode.event_index]
    row = frame.iloc[snapshot_index]
    atr = float(event["atr"])
    tick = TICKS[episode.symbol]
    entry_hint = float(frame.iloc[snapshot_index + 1]["open"])
    risk = max(tick, side * (entry_hint - episode.stop))
    path = frame.iloc[episode.event_index:snapshot_index + 1]
    favorable = float(path["high"].max()) - entry_hint if side > 0 else entry_hint - float(path["low"].min())
    adverse = entry_hint - float(path["low"].min()) if side > 0 else float(path["high"].max()) - entry_hint
    aligned_ret_3 = side * float(row.get("ret_3", np.nan))
    aligned_flow_3 = side * float(row.get("flow_3", np.nan))
    flow_abs = abs(float(row.get("flow_3", np.nan)))
    absorption_3 = flow_abs - abs(aligned_ret_3) if math.isfinite(flow_abs) and math.isfinite(aligned_ret_3) else np.nan
    aligned_ret_15 = side * float(row.get("ret_15", np.nan))
    aligned_flow_15 = side * float(row.get("flow_15", np.nan))
    absorption_10 = abs(aligned_flow_15) - abs(aligned_ret_15) if math.isfinite(aligned_flow_15) and math.isfinite(aligned_ret_15) else np.nan
    event_close_location = float(event.get("close_location", np.nan))
    if side < 0 and math.isfinite(event_close_location):
        event_close_location = 1.0 - event_close_location
    premium_z = float(row.get("premium_z", np.nan))
    premium_change = float(row.get("premium_change", np.nan))
    oi_change_5 = float(row.get("oi_change_5", np.nan))
    reversal_quality = aligned_ret_3 + aligned_flow_3
    return {
        "family_sweep": float(episode.family == "RANGE_SWEEP_REJECTION"),
        "family_acceptance": float(episode.family == "RANGE_ACCEPTANCE_PULLBACK"),
        "family_cascade": float(episode.family == "SYSTEMIC_FORCED_FLOW_EXHAUSTION"),
        "family_residual": float(episode.family == "COMMON_RESIDUAL_REJOIN"),
        "source_previous_8h": float(episode.source == "PREVIOUS_8H"),
        "source_previous_day": float(episode.source == "PREVIOUS_DAY"),
        "source_rolling_4h": float(episode.source == "ROLLING_4H"),
        "source_systemic": float(episode.source == "SYSTEMIC"),
        "source_common_factor": float(episode.source == "COMMON_FACTOR"),
        "level_age_hours": episode.level_age_hours,
        "block_progress": float(row.get("block_progress", np.nan)),
        "entry_delay_minutes": float(snapshot_index - episode.event_index),
        "planned_target_r": float(target_r),
        "risk_atr": risk / atr,
        "reward_atr": side * (target - entry_hint) / atr,
        "event_range_atr": episode.event_range_atr,
        "event_penetration_atr": episode.event_penetration_atr,
        "event_close_location": event_close_location,
        "event_displacement": side * float(event.get("ret_3", np.nan)),
        "progress_from_event_atr": side * (float(row["close"]) - float(event["close"])) / atr,
        "mfe_before_entry_r": favorable / risk,
        "mae_before_entry_r": adverse / risk,
        "aligned_ret_1": side * float(row.get("ret_1", np.nan)),
        "aligned_ret_3": aligned_ret_3,
        "aligned_ret_5": side * float(row.get("ret_5", np.nan)),
        "aligned_ret_15": aligned_ret_15,
        "aligned_ret_30": side * float(row.get("ret_30", np.nan)),
        "aligned_flow_1": side * float(row.get("flow_1", np.nan)),
        "aligned_flow_3": aligned_flow_3,
        "aligned_flow_5": side * float(row.get("flow_5", np.nan)),
        "aligned_flow_15": aligned_flow_15,
        "aligned_flow_30": side * float(row.get("flow_30", np.nan)),
        "flow_price_efficiency_3": float(row.get("flow_price_eff_3", np.nan)),
        "flow_price_efficiency_15": float(row.get("flow_price_eff_15", np.nan)),
        "activity_z": float(row.get("activity_z", np.nan)),
        "trade_count_z": float(row.get("trade_count_z", np.nan)),
        "volatility_ratio": float(row.get("volatility_ratio", np.nan)),
        "aligned_common_3": side * float(row.get("common_3", np.nan)),
        "aligned_common_15": side * float(row.get("common_15", np.nan)),
        "aligned_breadth_3": side * float(row.get("breadth_3", np.nan)),
        "aligned_breadth_15": side * float(row.get("breadth_15", np.nan)),
        "aligned_residual_z": side * float(row.get("residual_z", np.nan)),
        "aligned_residual_change_5": side * float(row.get("residual_change_5", np.nan)),
        "oi_change_5": oi_change_5,
        "oi_change_30": float(row.get("oi_change_30", np.nan)),
        "oi_price_agreement_5": side * oi_change_5 * float(row.get("ret_5", np.nan)),
        "taker_ratio_aligned": side * float(row.get("taker_ratio_log", np.nan)),
        "premium_aligned_z": side * premium_z,
        "premium_change_aligned": side * premium_change,
        "cross_asset_dispersion": float(row.get("cross_asset_dispersion", np.nan)),
        "common_impulse_z": side * float(row.get("common_impulse_z", np.nan)),
        "absorption_3": absorption_3,
        "absorption_10": absorption_10,
        "reversal_quality": reversal_quality if math.isfinite(reversal_quality) else np.nan,
        "objective_rank": float(objective_rank),
    }


def _snapshot_episode(episode: Episode, frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    decision_end = min(len(frame) - 2, episode.event_index + 15)
    for snapshot_index in range(episode.event_index, decision_end + 1):
        if snapshot_index > episode.event_index:
            path = frame.iloc[episode.event_index + 1:snapshot_index + 1]
            stop_touched = bool((path["low"] <= episode.stop).any()) if episode.side > 0 else bool((path["high"] >= episode.stop).any())
            nearest_target = episode.targets[0]
            target_touched = bool((path["high"] >= nearest_target).any()) if episode.side > 0 else bool((path["low"] <= nearest_target).any())
            if stop_touched or target_touched:
                break
        for objective_rank, (target, target_source) in enumerate(zip(episode.targets, episode.target_sources), start=1):
            label = _label_action(frame, snapshot_index + 1, episode, target)
            if label is None:
                continue
            snapshot_time = pd.Timestamp(frame.iloc[snapshot_index]["time"])
            row: dict[str, Any] = {
                "action_id": f"{episode.episode_id}:{snapshot_time.value}:{objective_rank}",
                "episode_id": episode.episode_id,
                "cluster_id": episode.cluster_id,
                "period": episode.period,
                "symbol": episode.symbol,
                "family": episode.family,
                "source": episode.source,
                "side": episode.side,
                "event_time": episode.event_time.isoformat(),
                "snapshot_time": snapshot_time.isoformat(),
                "target_source": target_source,
            }
            row.update(label)
            row.update(_features(frame, episode, snapshot_index, target, objective_rank, float(label["target_r"])))
            rows.append(row)
    return rows


def harvest(period: str, start: date, end: date, cache: Path, output: Path) -> None:
    warmup_start = start - timedelta(days=4)
    raw_frames: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        bars = load_range_flow(symbol, warmup_start, end, cache / "klines")
        metrics = load_metrics_range(symbol, warmup_start, end, cache / "derivatives")
        premium = load_premium_range(symbol, warmup_start, end, cache / "derivatives")
        raw_frames[symbol] = _prepare_symbol(attach_derivatives_context(bars, metrics, premium))
    frames = _attach_panel(raw_frames)
    start_time = pd.Timestamp(start, tz="UTC")
    end_time = pd.Timestamp(end + timedelta(days=1), tz="UTC") - pd.Timedelta(minutes=1)
    episodes: list[Episode] = []
    for symbol in SYMBOLS:
        episodes.extend(_range_episodes(period, symbol, frames[symbol], start_time, end_time))
        episodes.extend(_residual_episodes(period, symbol, frames[symbol], start_time, end_time))
    episodes.extend(_systemic_episodes(period, frames, start_time, end_time))
    episodes.sort(key=lambda item: (item.event_time, item.cluster_id, item.symbol, item.episode_id))

    actions: list[dict[str, Any]] = []
    for episode in episodes:
        actions.extend(_snapshot_episode(episode, frames[episode.symbol]))
    action_frame = pd.DataFrame(actions)
    if not action_frame.empty:
        action_frame = action_frame.sort_values(["entry_time", "cluster_id", "action_id"]).reset_index(drop=True)
        counts = action_frame.groupby("episode_id")["action_id"].transform("count").clip(lower=1)
        action_frame["episode_weight"] = 1.0 / counts
        if action_frame["action_id"].duplicated().any():
            raise RuntimeError("duplicate action identities")
        if (pd.to_datetime(action_frame["entry_time"], utc=True) <= pd.to_datetime(action_frame["snapshot_time"], utc=True)).any():
            raise RuntimeError("entry is not strictly after the decision snapshot")
    output.mkdir(parents=True, exist_ok=True)
    action_frame.to_csv(output / "actions.csv", index=False)
    episode_rows = []
    for episode in episodes:
        item = episode.__dict__.copy()
        item["event_time"] = episode.event_time.isoformat()
        item["targets"] = list(episode.targets)
        item["target_sources"] = list(episode.target_sources)
        episode_rows.append(item)
    pd.DataFrame(episode_rows).to_json(output / "episodes.jsonl", orient="records", lines=True)
    diagnostics = {
        "period": period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "episodes": len(episodes),
        "clusters": len({episode.cluster_id for episode in episodes}),
        "actions": int(len(action_frame)),
        "families": {family: sum(episode.family == family for episode in episodes) for family in FAMILIES},
        "symbols": {symbol: sum(episode.symbol == symbol for episode in episodes) for symbol in SYMBOLS},
        "features": list(FEATURE_COLUMNS),
        "causality": "COMPLETED_BAR_STATE__NEXT_OPEN_ENTRY__FROZEN_STOP_TARGET__THEN_FUTURE_FIRST_PASSAGE",
        "costs": {
            "taker_fee": TAKER_FEE,
            "maker_fee": MAKER_FEE,
            "entry_slip_ticks": ENTRY_SLIP_TICKS,
            "stop_slip_ticks": STOP_SLIP_TICKS,
            "funding_crossing_cost": FUNDING_CROSSING_COST,
        },
    }
    (output / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2, sort_keys=True), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", type=Path, default=Path(".cache/mechanism-v2"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    harvest(args.period, args.start, args.end, args.cache, args.output)


if __name__ == "__main__":
    main()
