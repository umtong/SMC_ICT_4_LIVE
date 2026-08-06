#!/usr/bin/env python3
"""Diagnose one-second impact resilience at causal five-minute liquidity pools.

This is a structural alpha screen, not a backtest engine. It creates no orders,
fills, fees, funding ledger, cash balance, PnL or NAV. A completed five-minute
pivot forms a public liquidity pool only after two completed bars on its right.
The literal first one-second touch opens a fixed fifteen-second auction window.
A reversal route is accepted only when completed OI already indicates inventory
release, attack-side quote flow is extreme relative to prior completed windows,
price impact is inefficient, the pool is reclaimed, and terminal flow changes
sign. Stops and targets are then fixed from observed event structure and
causally confirmed one-minute/five-minute pools. Only a route which passes this
screen is eligible for later NautilusTrader implementation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from data_microstructure_1s import load_microstructure_1s_bundle
from diagnose_failed_flow import aggregate_flow
from diagnose_session_handoff import _align_positioning
from smc_ict_4.manifest import write_json_atomic

NS_PER_SECOND = 1_000_000_000
NS_PER_MINUTE = 60 * NS_PER_SECOND
NS_PER_FIVE_MINUTES = 5 * NS_PER_MINUTE
NS_PER_FIFTEEN_SECONDS = 15 * NS_PER_SECOND


@dataclass(frozen=True, slots=True)
class Pool:
    pool_id: str
    timeframe: str
    side: str
    level: float
    pivot_ts_ns: int
    confirmed_ts_ns: int


@dataclass(frozen=True, slots=True)
class ImpactLogic:
    event_seconds: int = 15
    terminal_seconds: int = 3
    history_windows: int = 120
    flow_quantile: float = 0.90
    minimum_flow_multiple: float = 1.00
    minimum_attack_imbalance: float = 0.08
    minimum_penetration_atr: float = 0.03
    maximum_penetration_atr: float = 1.50
    maximum_impact_per_flow: float = 0.35
    maximum_path_efficiency: float = 0.45
    minimum_retrace_fraction: float = 0.80
    reclaim_buffer_atr: float = 0.01
    minimum_terminal_opposite_imbalance: float = 0.05
    minimum_terminal_body_atr: float = 0.01
    stop_buffer_atr: float = 0.05
    minimum_rr: float = 1.25
    one_minute_pivot_radius: int = 2
    five_minute_pivot_radius: int = 2
    minute_atr_period: int = 60
    oi_period: int = 36
    oi_impulse_rank: float = 0.50

    def validate(self) -> None:
        for name in (
            "event_seconds",
            "terminal_seconds",
            "history_windows",
            "one_minute_pivot_radius",
            "five_minute_pivot_radius",
            "minute_atr_period",
            "oi_period",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.terminal_seconds > self.event_seconds:
            raise ValueError("terminal_seconds cannot exceed event_seconds")
        for name in (
            "flow_quantile",
            "minimum_retrace_fraction",
            "oi_impulse_rank",
        ):
            if not 0.0 <= float(getattr(self, name)) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if not 0.0 < self.minimum_attack_imbalance < 1.0:
            raise ValueError("minimum_attack_imbalance must be in (0, 1)")
        if not 0.0 <= self.minimum_terminal_opposite_imbalance < 1.0:
            raise ValueError("terminal imbalance must be in [0, 1)")
        if not 0.0 < self.minimum_penetration_atr < self.maximum_penetration_atr:
            raise ValueError("penetration bounds are inconsistent")
        if self.minimum_flow_multiple <= 0.0:
            raise ValueError("minimum_flow_multiple must be positive")
        if self.maximum_impact_per_flow <= 0.0:
            raise ValueError("maximum_impact_per_flow must be positive")
        if not 0.0 <= self.maximum_path_efficiency <= 1.0:
            raise ValueError("maximum_path_efficiency must be in [0, 1]")
        if self.stop_buffer_atr < 0.0 or self.minimum_rr <= 0.0:
            raise ValueError("geometry parameters are inconsistent")


def _utc_ns(value: date) -> int:
    return int(
        datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        * 1e9
    )


def _minute_features(frame: pd.DataFrame, *, atr_period: int) -> pd.DataFrame:
    work = frame.copy()
    work["timestamp_ns"] = work.index.map(lambda value: int(value.value)).astype("int64")
    previous = work["close"].shift(1)
    true_range = pd.concat(
        [
            work["high"] - work["low"],
            (work["high"] - previous).abs(),
            (work["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    work["atr"] = true_range.shift(1).rolling(
        atr_period,
        min_periods=atr_period,
    ).mean()
    return work.reset_index(drop=True)


def _pool_confirmations(
    bars: pd.DataFrame,
    *,
    timeframe: str,
    radius: int,
) -> list[Pool]:
    if radius <= 0:
        raise ValueError("radius must be positive")
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    output: list[Pool] = []
    for center in range(radius, len(bars.index) - radius):
        left = slice(center - radius, center)
        right = slice(center + 1, center + radius + 1)
        pivot_ns = int(timestamps[center])
        confirmed_ns = int(timestamps[center + radius])
        high = float(highs[center])
        low = float(lows[center])
        if high > float(np.max(highs[left])) and high > float(np.max(highs[right])):
            output.append(
                Pool(
                    pool_id=f"{timeframe}H-{pivot_ns}",
                    timeframe=timeframe,
                    side="UPPER",
                    level=high,
                    pivot_ts_ns=pivot_ns,
                    confirmed_ts_ns=confirmed_ns,
                )
            )
        if low < float(np.min(lows[left])) and low < float(np.min(lows[right])):
            output.append(
                Pool(
                    pool_id=f"{timeframe}L-{pivot_ns}",
                    timeframe=timeframe,
                    side="LOWER",
                    level=low,
                    pivot_ts_ns=pivot_ns,
                    confirmed_ts_ns=confirmed_ns,
                )
            )
    output.sort(key=lambda pool: (pool.confirmed_ts_ns, pool.pool_id))
    return output


def _attach_causal_context(
    seconds: pd.DataFrame,
    minute: pd.DataFrame,
    five: pd.DataFrame,
    *,
    history_windows: int,
    flow_quantile: float,
) -> pd.DataFrame:
    work = seconds.copy().reset_index(drop=True)
    work["timestamp_ns"] = work["close_time_ns"].astype("int64")
    work = work.sort_values("timestamp_ns", kind="stable").reset_index(drop=True)

    minute_context = minute[["timestamp_ns", "atr"]].dropna().copy()
    minute_context["timestamp_ns"] = minute_context["timestamp_ns"].astype("int64")
    minute_context = minute_context.rename(columns={"timestamp_ns": "atr_available_ns"})
    work = pd.merge_asof(
        work,
        minute_context.sort_values("atr_available_ns"),
        left_on="timestamp_ns",
        right_on="atr_available_ns",
        direction="backward",
        allow_exact_matches=True,
        tolerance=2 * NS_PER_MINUTE,
    )

    if "snapshot_ns" not in five.columns:
        raise RuntimeError("aligned five-minute positioning frame lacks snapshot_ns")
    positioning = five[
        [
            "snapshot_ns",
            "positioning_valid",
            "inventory_state",
            "sum_open_interest",
            "oi_change_fraction",
            "oi_impulse_rank",
        ]
    ].copy()
    positioning["snapshot_ns"] = positioning["snapshot_ns"].astype("int64")
    positioning = positioning.sort_values("snapshot_ns", kind="stable")
    work = pd.merge_asof(
        work,
        positioning,
        left_on="timestamp_ns",
        right_on="snapshot_ns",
        direction="backward",
        allow_exact_matches=True,
        tolerance=NS_PER_FIVE_MINUTES + NS_PER_SECOND,
    )

    work["taker_sell_quote"] = (
        work["quote_volume"] - work["taker_buy_quote"]
    ).clip(lower=0.0)
    work["signed_quote"] = work["taker_buy_quote"] - work["taker_sell_quote"]
    work["bucket_15s"] = work["timestamp_ns"] // NS_PER_FIFTEEN_SECONDS
    grouped = work.groupby("bucket_15s", sort=True)
    windows = grouped.agg(
        buy_quote=("taker_buy_quote", "sum"),
        sell_quote=("taker_sell_quote", "sum"),
        count=("timestamp_ns", "count"),
    ).reset_index()
    windows["buy_q"] = windows["buy_quote"].shift(1).rolling(
        history_windows,
        min_periods=history_windows,
    ).quantile(flow_quantile)
    windows["sell_q"] = windows["sell_quote"].shift(1).rolling(
        history_windows,
        min_periods=history_windows,
    ).quantile(flow_quantile)
    work = work.merge(
        windows[["bucket_15s", "buy_q", "sell_q", "count"]],
        on="bucket_15s",
        how="left",
        sort=False,
    )
    return work


def _first_touch_index(
    pool: Pool,
    *,
    timestamps: np.ndarray,
    previous_close: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    stop_index: int | None = None,
) -> int | None:
    start = int(np.searchsorted(timestamps, pool.confirmed_ts_ns, side="right"))
    end = len(timestamps) if stop_index is None else min(len(timestamps), stop_index + 1)
    if start >= end:
        return None
    if pool.side == "UPPER":
        mask = (previous_close[start:end] <= pool.level) & (highs[start:end] >= pool.level)
    else:
        mask = (previous_close[start:end] >= pool.level) & (lows[start:end] <= pool.level)
    hits = np.flatnonzero(mask)
    return None if len(hits) == 0 else start + int(hits[0])


def _path_result(
    bars: pd.DataFrame,
    *,
    start_index: int,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    max_hold_seconds: int,
) -> tuple[dict[str, Any], int]:
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0.0:
        raise ValueError("risk must be positive")
    end = min(len(bars.index), start_index + 1 + max_hold_seconds)
    path = bars.iloc[start_index + 1 : end]
    max_favorable = 0.0
    max_adverse = 0.0
    terminal_index = max(start_index, end - 1)
    terminal_outcome = "TIMEOUT"
    terminal_ns: int | None = None
    terminal_close = entry

    for index, row in path.iterrows():
        if direction == "LONG":
            favorable = (float(row["high"]) - entry) / risk
            adverse = (entry - float(row["low"])) / risk
            stop_hit = float(row["low"]) <= stop
            target_hit = float(row["high"]) >= target
        else:
            favorable = (entry - float(row["low"])) / risk
            adverse = (float(row["high"]) - entry) / risk
            stop_hit = float(row["high"]) >= stop
            target_hit = float(row["low"]) <= target
        max_favorable = max(max_favorable, favorable)
        max_adverse = max(max_adverse, adverse)
        terminal_close = float(row["close"])
        if stop_hit and target_hit:
            terminal_outcome = "AMBIGUOUS_SAME_SECOND"
        elif stop_hit:
            terminal_outcome = "STOP"
        elif target_hit:
            terminal_outcome = "TARGET"
        else:
            continue
        terminal_index = int(index)
        terminal_ns = int(row["timestamp_ns"])
        break
    else:
        if not path.empty:
            terminal_index = int(path.index[-1])
            terminal_ns = int(path.iloc[-1]["timestamp_ns"])

    terminal_r = (
        (terminal_close - entry) / risk
        if direction == "LONG"
        else (entry - terminal_close) / risk
    )
    return (
        {
            "outcome": terminal_outcome,
            "terminal_timestamp_ns": terminal_ns,
            "mfe_r": max_favorable,
            "mae_r": max_adverse,
            "terminal_close_r": terminal_r,
        },
        terminal_index,
    )


def _target_pool(
    pools_by_timeframe: Mapping[str, Iterable[Pool]],
    *,
    direction: str,
    entry: float,
    stop: float,
    entry_index: int,
    timestamps: np.ndarray,
    previous_close: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    touch_cache: dict[str, int | None],
    minimum_rr: float,
) -> tuple[Pool, float] | None:
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0.0:
        return None
    side = "UPPER" if direction == "LONG" else "LOWER"
    for timeframe in ("1M", "5M"):
        candidates = [
            pool
            for pool in pools_by_timeframe.get(timeframe, ())
            if pool.confirmed_ts_ns < int(timestamps[entry_index])
            and pool.side == side
            and (pool.level > entry if direction == "LONG" else pool.level < entry)
        ]
        candidates.sort(key=lambda pool: abs(pool.level - entry))
        for pool in candidates:
            rr = abs(pool.level - entry) / risk
            if rr < minimum_rr:
                continue
            if pool.pool_id not in touch_cache:
                touch_cache[pool.pool_id] = _first_touch_index(
                    pool,
                    timestamps=timestamps,
                    previous_close=previous_close,
                    highs=highs,
                    lows=lows,
                )
            first_touch = touch_cache[pool.pool_id]
            if first_touch is None or first_touch > entry_index:
                return pool, rr
    return None


def _payload(row: pd.Series) -> dict[str, Any]:
    return {
        "timestamp_ns": int(row["timestamp_ns"]),
        "timestamp": pd.to_datetime(int(row["timestamp_ns"]), unit="ns", utc=True).isoformat(),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "quote_volume": float(row["quote_volume"]),
        "taker_buy_quote": float(row["taker_buy_quote"]),
        "taker_sell_quote": float(row["taker_sell_quote"]),
        "signed_quote": float(row["signed_quote"]),
        "mark_close": None if pd.isna(row["mark_close"]) else float(row["mark_close"]),
        "index_close": None if pd.isna(row["index_close"]) else float(row["index_close"]),
        "atr": None if pd.isna(row["atr"]) else float(row["atr"]),
        "positioning_valid": bool(row.get("positioning_valid", False)),
        "inventory_state": str(row.get("inventory_state", "INVALID")),
        "open_interest": (
            None if pd.isna(row.get("sum_open_interest")) else float(row["sum_open_interest"])
        ),
        "oi_change_fraction": (
            None if pd.isna(row.get("oi_change_fraction")) else float(row["oi_change_fraction"])
        ),
        "oi_impulse_rank": (
            None if pd.isna(row.get("oi_impulse_rank")) else float(row["oi_impulse_rank"])
        ),
    }


def diagnose(
    bars: pd.DataFrame,
    *,
    source_pools: Iterable[Pool],
    target_pools: Mapping[str, Iterable[Pool]],
    trade_start_ns: int,
    trade_end_ns: int,
    max_hold_seconds: int,
    logic: ImpactLogic,
) -> dict[str, Any]:
    logic.validate()
    source_pool_list = list(source_pools)
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    closes = bars["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]

    contact_candidates: list[tuple[int, Pool]] = []
    for pool in source_pool_list:
        touch = _first_touch_index(
            pool,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
        )
        if touch is not None:
            contact_candidates.append((touch, pool))
    contact_candidates.sort(key=lambda item: (item[0], item[1].pool_id))

    counters: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    block_until = -1
    target_touch_cache: dict[str, int | None] = {}

    for contact_index, pool in contact_candidates:
        timestamp_ns = int(timestamps[contact_index])
        if not trade_start_ns <= timestamp_ns < trade_end_ns:
            counters["CONTACT_OUTSIDE_TRADE_INTERVAL"] += 1
            continue
        if contact_index <= block_until:
            counters["CONTACT_DURING_ACTIVE_SLOT"] += 1
            continue
        if contact_index + logic.event_seconds > len(bars.index):
            counters["INCOMPLETE_EVENT_WINDOW"] += 1
            continue

        contact = bars.iloc[contact_index]
        if pd.isna(contact["atr"]) or float(contact["atr"]) <= 0.0:
            counters["NO_CAUSAL_ATR"] += 1
            continue
        if not bool(contact.get("positioning_valid", False)):
            counters["POSITIONING_INVALID"] += 1
            continue
        if str(contact.get("inventory_state")) != "RELEASE":
            counters[f"CONTACT_{str(contact.get('inventory_state'))}"] += 1
            continue
        if not bool(contact.get("reference_valid", False)):
            counters["REFERENCE_INVALID_AT_CONTACT"] += 1
            continue
        q90 = float(contact["buy_q"] if pool.side == "UPPER" else contact["sell_q"])
        if not np.isfinite(q90) or q90 <= 0.0:
            counters["FLOW_REFERENCE_WARMUP"] += 1
            continue

        window = bars.iloc[contact_index : contact_index + logic.event_seconds]
        if len(window.index) != logic.event_seconds:
            counters["INCOMPLETE_EVENT_WINDOW"] += 1
            continue
        if not bool(window["reference_valid"].all()):
            counters["REFERENCE_GAP_IN_EVENT"] += 1
            continue
        diffs = window["timestamp_ns"].astype("int64").diff().dropna()
        if bool((diffs != NS_PER_SECOND).any()):
            counters["ONE_SECOND_GAP_IN_EVENT"] += 1
            continue

        atr = float(contact["atr"])
        buy_quote = float(window["taker_buy_quote"].sum())
        sell_quote = float(window["taker_sell_quote"].sum())
        total_quote = buy_quote + sell_quote
        if total_quote <= 0.0:
            counters["ZERO_EVENT_FLOW"] += 1
            continue
        signed_imbalance = (buy_quote - sell_quote) / total_quote
        attack_quote = buy_quote if pool.side == "UPPER" else sell_quote
        flow_multiple = attack_quote / q90
        attack_imbalance = signed_imbalance if pool.side == "UPPER" else -signed_imbalance

        event_open = float(window.iloc[0]["open"])
        terminal_close = float(window.iloc[-1]["close"])
        close_path = np.concatenate(([event_open], window["close"].astype(float).to_numpy()))
        path_length = float(np.abs(np.diff(close_path)).sum())
        path_efficiency = abs(terminal_close - event_open) / path_length if path_length > 0.0 else 0.0
        if pool.side == "UPPER":
            event_extreme = float(window["high"].max())
            penetration = event_extreme - pool.level
            retrace_fraction = (event_extreme - terminal_close) / max(penetration, 1e-12)
            reclaimed = terminal_close < pool.level - logic.reclaim_buffer_atr * atr
            direction = "SHORT"
        else:
            event_extreme = float(window["low"].min())
            penetration = pool.level - event_extreme
            retrace_fraction = (terminal_close - event_extreme) / max(penetration, 1e-12)
            reclaimed = terminal_close > pool.level + logic.reclaim_buffer_atr * atr
            direction = "LONG"
        penetration_atr = penetration / atr
        impact_per_flow = penetration_atr / max(flow_multiple, 1e-12)

        terminal = window.iloc[-logic.terminal_seconds :]
        terminal_buy = float(terminal["taker_buy_quote"].sum())
        terminal_sell = float(terminal["taker_sell_quote"].sum())
        terminal_total = terminal_buy + terminal_sell
        terminal_imbalance = (
            (terminal_buy - terminal_sell) / terminal_total if terminal_total > 0.0 else 0.0
        )
        terminal_body = float(terminal.iloc[-1]["close"]) - float(terminal.iloc[0]["open"])
        opposite_flow = (
            terminal_imbalance <= -logic.minimum_terminal_opposite_imbalance
            if direction == "SHORT"
            else terminal_imbalance >= logic.minimum_terminal_opposite_imbalance
        )
        opposite_body = (
            terminal_body <= -logic.minimum_terminal_body_atr * atr
            if direction == "SHORT"
            else terminal_body >= logic.minimum_terminal_body_atr * atr
        )

        conditions = {
            "flow_multiple": flow_multiple >= logic.minimum_flow_multiple,
            "attack_imbalance": attack_imbalance >= logic.minimum_attack_imbalance,
            "penetration": logic.minimum_penetration_atr <= penetration_atr <= logic.maximum_penetration_atr,
            "impact_per_flow": impact_per_flow <= logic.maximum_impact_per_flow,
            "path_efficiency": path_efficiency <= logic.maximum_path_efficiency,
            "retrace_fraction": retrace_fraction >= logic.minimum_retrace_fraction,
            "pool_reclaim": reclaimed,
            "terminal_opposite_flow": opposite_flow,
            "terminal_opposite_body": opposite_body,
        }
        failed = [name for name, passed in conditions.items() if not passed]

        trade_close = window["close"].astype(float)
        mark_close = window["mark_close"].astype(float)
        index_close = window["index_close"].astype(float)
        initial_basis = float((trade_close.iloc[0] - index_close.iloc[0]) / index_close.iloc[0])
        terminal_basis = float((trade_close.iloc[-1] - index_close.iloc[-1]) / index_close.iloc[-1])
        diagnostic = {
            "pool_id": pool.pool_id,
            "pool_side": pool.side,
            "liquidity_level": pool.level,
            "contact": _payload(contact),
            "event_terminal": _payload(window.iloc[-1]),
            "direction": direction,
            "flow_multiple": flow_multiple,
            "attack_imbalance": attack_imbalance,
            "penetration_atr": penetration_atr,
            "impact_per_flow": impact_per_flow,
            "path_efficiency": path_efficiency,
            "retrace_fraction": retrace_fraction,
            "terminal_imbalance": terminal_imbalance,
            "terminal_body_atr": terminal_body / atr,
            "basis_change_bps": (terminal_basis - initial_basis) * 10_000.0,
            "mark_move_atr": float(mark_close.iloc[-1] - mark_close.iloc[0]) / atr,
            "index_move_atr": float(index_close.iloc[-1] - index_close.iloc[0]) / atr,
            "conditions": conditions,
        }
        if failed:
            counters[f"REJECT_{failed[0].upper()}"] += 1
            scenarios.append(
                {
                    "scenario_id": f"c07ir-{timestamp_ns}-{pool.pool_id}",
                    "outcome": "EVENT_REJECTED",
                    "failed_conditions": failed,
                    **diagnostic,
                }
            )
            continue

        entry_index = contact_index + logic.event_seconds - 1
        entry = terminal_close
        stop = (
            event_extreme + logic.stop_buffer_atr * atr
            if direction == "SHORT"
            else event_extreme - logic.stop_buffer_atr * atr
        )
        risk = stop - entry if direction == "SHORT" else entry - stop
        if risk <= 0.0:
            counters["NONPOSITIVE_RISK"] += 1
            continue
        selected = _target_pool(
            target_pools,
            direction=direction,
            entry=entry,
            stop=stop,
            entry_index=entry_index,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
            touch_cache=target_touch_cache,
            minimum_rr=logic.minimum_rr,
        )
        if selected is None:
            counters["NO_CAUSAL_TARGET_AT_MINIMUM_RR"] += 1
            scenarios.append(
                {
                    "scenario_id": f"c07ir-{timestamp_ns}-{pool.pool_id}",
                    "outcome": "NO_CAUSAL_TARGET_AT_MINIMUM_RR",
                    "entry": entry,
                    "stop": stop,
                    "risk": risk,
                    **diagnostic,
                }
            )
            continue
        target_pool, expected_rr = selected
        path, terminal_index = _path_result(
            bars,
            start_index=entry_index,
            direction=direction,
            entry=entry,
            stop=stop,
            target=target_pool.level,
            max_hold_seconds=max_hold_seconds,
        )
        block_until = max(block_until, terminal_index)
        counters["ENTRY_READY"] += 1
        scenarios.append(
            {
                "scenario_id": f"c07ir-{timestamp_ns}-{pool.pool_id}",
                "outcome": "ENTRY_READY",
                "entry": entry,
                "stop": stop,
                "risk": risk,
                "target": target_pool.level,
                "target_pool_id": target_pool.pool_id,
                "target_timeframe": target_pool.timeframe,
                "expected_rr": expected_rr,
                "path": path,
                **diagnostic,
            }
        )

    entries = [item for item in scenarios if item.get("outcome") == "ENTRY_READY"]
    outcomes = Counter(str(item["path"]["outcome"]) for item in entries)
    active_dates = [
        pd.to_datetime(int(item["contact"]["timestamp_ns"]), unit="ns", utc=True).date().isoformat()
        for item in entries
    ]
    date_counts = Counter(active_dates)
    mfe = [float(item["path"]["mfe_r"]) for item in entries]
    mae = [float(item["path"]["mae_r"]) for item in entries]
    max_day_share = max(date_counts.values()) / len(entries) if entries and date_counts else None
    gate = {
        "minimum_entry_ready": len(entries) >= 7,
        "minimum_active_days": len(date_counts) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "median_mfe_at_least_minimum_rr": bool(mfe) and float(pd.Series(mfe).median()) >= logic.minimum_rr,
        "median_mae_below_one_r": bool(mae) and float(pd.Series(mae).median()) < 1.0,
        "maximum_day_share_at_most_55pct": max_day_share is not None and max_day_share <= 0.55,
    }
    gate["passed"] = all(gate.values())
    return {
        "summary": {
            "source_pools": len(source_pool_list),
            "source_pools_touched": len(contact_candidates),
            "contact_counts": dict(sorted(counters.items())),
            "entry_ready": len(entries),
            "active_days": len(date_counts),
            "entries_by_day": dict(sorted(date_counts.items())),
            "maximum_day_share": max_day_share,
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
            "median_mae_r": float(pd.Series(mae).median()) if mae else None,
            "diagnostic_gate": gate,
        },
        "scenarios": scenarios,
    }


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    logic = ImpactLogic()
    logic.validate()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = load_microstructure_1s_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        positioning_warmup_days=int(config["warmup_days"]),
        micro_warmup_days=args.micro_warmup_days,
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("impact_resilience_1s_data_manifest.json"),
    )

    minute = _minute_features(
        bundle.minute_positioning.frame,
        atr_period=logic.minute_atr_period,
    )
    five = aggregate_flow(bundle.minute_positioning.frame, 5, 36)
    five = _align_positioning(
        five,
        bundle.minute_positioning.metrics,
        oi_period=logic.oi_period,
        oi_impulse_rank=logic.oi_impulse_rank,
    )
    five["timestamp_ns"] = five["timestamp_ns"].astype("int64")
    bars = _attach_causal_context(
        bundle.seconds,
        minute,
        five,
        history_windows=logic.history_windows,
        flow_quantile=logic.flow_quantile,
    )

    one_minute_pools = _pool_confirmations(
        minute,
        timeframe="1M",
        radius=logic.one_minute_pivot_radius,
    )
    five_minute_pools = _pool_confirmations(
        five,
        timeframe="5M",
        radius=logic.five_minute_pivot_radius,
    )
    result = diagnose(
        bars,
        source_pools=five_minute_pools,
        target_pools={"1M": one_minute_pools, "5M": five_minute_pools},
        trade_start_ns=_utc_ns(args.start),
        trade_end_ns=_utc_ns(args.end),
        max_hold_seconds=int(config["max_hold_minutes"]) * 60,
        logic=logic,
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "hypothesis": "one-second impact resilience after OI-release liquidity attack",
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "logic": {name: getattr(logic, name) for name in logic.__dataclass_fields__},
        "data_contract": {
            "trade_mark_index": "checksum-verified Binance USD-M official one-second klines",
            "positioning": "completed public five-minute OI metrics; gaps invalidate state",
            "contact_pool": "five-minute pivot confirmed after two completed right-side bars",
            "event_window": "fixed fifteen completed one-second observations from literal first touch",
            "flow_reference": "prior 120 completed non-overlapping fifteen-second windows",
            "target_hierarchy": "causally confirmed one-minute then five-minute pools",
            "pool_reuse": False,
            "single_pending_or_open_slot": True,
            "future_information": False,
            "orders_or_pnl": False,
        },
        "cadence_gaps": bundle.cadence_gaps,
        **result,
    }
    write_json_atomic(output, payload)
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--stage", default="week-1")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path(".research-data/candidate-07"))
    parser.add_argument("--micro-warmup-days", type=int, default=1)
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
