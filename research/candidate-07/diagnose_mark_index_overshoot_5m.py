#!/usr/bin/env python3
"""Diagnose mark-confirmed futures liquidation overshoot at five-minute pools.

A traded-price wick is not assumed to be a forced liquidation.  The candidate
requires a causally confirmed five-minute perpetual pool to be penetrated by
both last-trade price and mark price while the spot index does not transfer the
move.  Completed aggressor flow and the latest completed five-minute OI state
must show an attack with OI release, and the mark/index premium must expand in
the attack direction.  A prompt joint trade/mark reclaim with opposite flow
then routes one reversal toward the nearest active causal liquidity pool.

The diagnostic separates detector, scenario and execution geometry.  It creates
no orders, fills, fees, funding ledger, PnL, cash or NAV and is not a replacement
backtest engine.  Promotion requires the frozen structural gate before a
NautilusTrader strategy is implemented.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from data_mark_index_reference import load_mark_index_positioning_bundle
from diagnose_cross_market_transfer_1m import (
    align_positioning_asof,
    five_minute_bars,
    minute_market_frame,
    pivot_confirmations,
    positioning_states,
)
from diagnose_inventory_handoff import _directional_body, _same_direction_flow
from diagnose_inventory_handoff_exit_safe import _exit_safe_path_result
from diagnose_inventory_pressure_continuation import _copy_pool, _utc_ns
from diagnose_mtf_liquidity import Pool, context_bars, pool_confirmations
from smc_ict_4.manifest import write_json_atomic


@dataclass(slots=True)
class MarkIndexPool:
    pool_id: str
    side: str
    trade_level: float
    mark_level: float
    index_level: float
    pivot_ts_ns: int
    confirmed_ts_ns: int
    consumed: bool = False
    consumed_ts_ns: int | None = None


@dataclass(slots=True)
class OvershootEpisode:
    scenario_id: str
    contact_index: int
    pool_id: str
    side: str
    trade_level: float
    mark_level: float
    index_level: float
    trade_direction: str
    contact_trade_extreme: float
    contact_mark_extreme: float
    contact_premium: float
    contact_oi: float
    trade_atr: float
    mark_atr: float
    index_atr: float
    trade_penetration_atr: float
    mark_penetration_atr: float
    index_penetration_atr: float
    mark_transfer_ratio: float
    index_transfer_ratio: float
    premium_change_rank: float
    directional_premium_change_bps: float


@dataclass(frozen=True, slots=True)
class OvershootLogic:
    minute_atr_period: int = 60
    minute_flow_period: int = 60
    premium_rank_period: int = 120
    oi_period: int = 36
    oi_impulse_rank: float = 0.50
    contact_min_atr: float = 0.10
    contact_max_atr: float = 3.00
    attack_imbalance: float = 0.08
    attack_flow_z: float = 0.25
    minimum_mark_transfer_ratio: float = 0.50
    maximum_index_transfer_ratio: float = 0.35
    minimum_premium_change_rank: float = 0.60
    confirmation_minutes: int = 3
    confirmation_body_atr: float = 0.15
    confirmation_imbalance: float = 0.03
    reclaim_buffer_atr: float = 0.02
    stop_buffer_atr: float = 0.10
    minimum_rr: float = 1.25
    max_hold_minutes: int = 120
    contact_pivot_radius: int = 2
    one_minute_target_radius: int = 2
    five_minute_target_radius: int = 2

    def validate(self) -> None:
        for name in (
            "minute_atr_period",
            "minute_flow_period",
            "premium_rank_period",
            "oi_period",
            "confirmation_minutes",
            "max_hold_minutes",
            "contact_pivot_radius",
            "one_minute_target_radius",
            "five_minute_target_radius",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0.0 <= self.oi_impulse_rank <= 1.0:
            raise ValueError("oi_impulse_rank must be in [0, 1]")
        if not 0.0 < self.contact_min_atr < self.contact_max_atr:
            raise ValueError("contact penetration bounds are inconsistent")
        if not 0.0 < self.attack_imbalance < 1.0:
            raise ValueError("attack_imbalance must be in (0, 1)")
        if not 0.0 <= self.confirmation_imbalance < 1.0:
            raise ValueError("confirmation_imbalance must be in [0, 1)")
        if not 0.0 < self.minimum_mark_transfer_ratio:
            raise ValueError("minimum_mark_transfer_ratio must be positive")
        if not 0.0 <= self.maximum_index_transfer_ratio < self.minimum_mark_transfer_ratio:
            raise ValueError("mark/index transfer classes overlap")
        if not 0.0 <= self.minimum_premium_change_rank <= 1.0:
            raise ValueError("premium change rank must be in [0, 1]")
        if self.minimum_rr <= 0.0:
            raise ValueError("minimum_rr must be positive")


def _reference_atr(frame: pd.DataFrame, period: int, prefix: str) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index.copy())
    for name in ("open", "high", "low", "close"):
        output[f"{prefix}_{name}"] = frame[name].to_numpy()
    previous = output[f"{prefix}_close"].shift(1)
    true_range = pd.concat(
        [
            output[f"{prefix}_high"] - output[f"{prefix}_low"],
            (output[f"{prefix}_high"] - previous).abs(),
            (output[f"{prefix}_low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    output[f"{prefix}_atr"] = true_range.shift(1).rolling(
        period,
        min_periods=period,
    ).mean()
    return output.reset_index(drop=True)


def minute_frame(bundle, logic: OvershootLogic) -> pd.DataFrame:
    bars = minute_market_frame(
        bundle.frame,
        bundle.index_frame,
        atr_period=logic.minute_atr_period,
        flow_period=logic.minute_flow_period,
        basis_rank_period=logic.premium_rank_period,
    )
    mark = _reference_atr(bundle.mark_frame, logic.minute_atr_period, "mark")
    for name in ("mark_open", "mark_high", "mark_low", "mark_close", "mark_atr"):
        bars[name] = mark[name].to_numpy()
    bars["mark_index_premium"] = (
        (bars["mark_close"] - bars["index_close"]) / bars["index_close"]
    )
    bars["premium_change"] = bars["mark_index_premium"].diff()
    changes = bars["premium_change"].abs().tolist()
    ranks: list[float | None] = []
    for index, current in enumerate(changes):
        if index == 0 or current != current:
            ranks.append(None)
            continue
        past = [
            float(value)
            for value in changes[max(1, index - logic.premium_rank_period) : index]
            if value == value
        ]
        ranks.append(
            None
            if len(past) < logic.premium_rank_period
            else sum(value <= float(current) for value in past) / len(past)
        )
    bars["premium_change_rank"] = ranks
    bars["timestamp"] = pd.to_datetime(bars["timestamp_ns"], unit="ns", utc=True)
    return bars


def _aggregate_reference(frame: pd.DataFrame, minutes: int, prefix: str) -> pd.DataFrame:
    work = frame.copy()
    work["bucket"] = [index // minutes for index in range(len(work.index))]
    grouped = work.groupby("bucket", sort=True)
    bars = grouped.agg(
        **{
            f"{prefix}_open": ("open", "first"),
            f"{prefix}_high": ("high", "max"),
            f"{prefix}_low": ("low", "min"),
            f"{prefix}_close": ("close", "last"),
        }
    )
    bars["timestamp_ns"] = grouped.apply(
        lambda part: int(part.index[-1].value),
        include_groups=False,
    )
    return bars.reset_index(drop=True)


def mark_index_pool_confirmations(
    trade_five: pd.DataFrame,
    mark_five: pd.DataFrame,
    index_five: pd.DataFrame,
    *,
    radius: int,
) -> dict[int, list[MarkIndexPool]]:
    if not trade_five["timestamp_ns"].equals(mark_five["timestamp_ns"]):
        raise RuntimeError("five-minute trade and mark timestamps differ")
    if not trade_five["timestamp_ns"].equals(index_five["timestamp_ns"]):
        raise RuntimeError("five-minute trade and index timestamps differ")
    mark_by_ns = {
        int(row.timestamp_ns): row
        for row in mark_five.itertuples(index=False)
    }
    index_by_ns = {
        int(row.timestamp_ns): row
        for row in index_five.itertuples(index=False)
    }
    output: dict[int, list[MarkIndexPool]] = {}
    for confirmation_ns, pools in pivot_confirmations(
        trade_five,
        radius=radius,
        prefix="C5",
    ).items():
        mapped: list[MarkIndexPool] = []
        for pool in pools:
            mark_row = mark_by_ns[int(pool.pivot_ts_ns)]
            index_row = index_by_ns[int(pool.pivot_ts_ns)]
            mapped.append(
                MarkIndexPool(
                    pool_id=f"MIX5:{pool.pool_id}",
                    side=pool.side,
                    trade_level=float(pool.level),
                    mark_level=(
                        float(mark_row.mark_high)
                        if pool.side == "UPPER"
                        else float(mark_row.mark_low)
                    ),
                    index_level=(
                        float(index_row.index_high)
                        if pool.side == "UPPER"
                        else float(index_row.index_low)
                    ),
                    pivot_ts_ns=int(pool.pivot_ts_ns),
                    confirmed_ts_ns=int(pool.confirmed_ts_ns),
                )
            )
        output[int(confirmation_ns)] = mapped
    return output


def _payload(row: pd.Series) -> dict[str, Any]:
    return {
        "timestamp_ns": int(row["timestamp_ns"]),
        "timestamp": row["timestamp"].isoformat(),
        "trade_open": float(row["open"]),
        "trade_high": float(row["high"]),
        "trade_low": float(row["low"]),
        "trade_close": float(row["close"]),
        "mark_open": float(row["mark_open"]),
        "mark_high": float(row["mark_high"]),
        "mark_low": float(row["mark_low"]),
        "mark_close": float(row["mark_close"]),
        "index_open": float(row["index_open"]),
        "index_high": float(row["index_high"]),
        "index_low": float(row["index_low"]),
        "index_close": float(row["index_close"]),
        "trade_atr": None if pd.isna(row["atr"]) else float(row["atr"]),
        "mark_atr": None if pd.isna(row["mark_atr"]) else float(row["mark_atr"]),
        "index_atr": None if pd.isna(row["index_atr"]) else float(row["index_atr"]),
        "mark_index_premium_bps": float(row["mark_index_premium"]) * 10_000.0,
        "premium_change_bps": None if pd.isna(row["premium_change"]) else float(row["premium_change"]) * 10_000.0,
        "premium_change_rank": None if pd.isna(row["premium_change_rank"]) else float(row["premium_change_rank"]),
        "imbalance": float(row["imbalance"]),
        "flow_z": float(row["flow_z"]),
        "snapshot_ns": None if pd.isna(row["snapshot_ns"]) else int(row["snapshot_ns"]),
        "snapshot_age_ns": None if pd.isna(row["snapshot_age_ns"]) else int(row["snapshot_age_ns"]),
        "positioning_valid": bool(row["positioning_valid"]),
        "open_interest": None if pd.isna(row["sum_open_interest"]) else float(row["sum_open_interest"]),
        "oi_change_fraction": None if pd.isna(row["oi_change_fraction"]) else float(row["oi_change_fraction"]),
        "oi_impulse_rank": None if pd.isna(row["oi_impulse_rank"]) else float(row["oi_impulse_rank"]),
        "inventory_state": str(row["inventory_state"]),
    }


def _contacts(
    pools: Mapping[str, MarkIndexPool],
    row: pd.Series,
    previous_close: float,
    timestamp_ns: int,
) -> tuple[list[MarkIndexPool], list[MarkIndexPool]]:
    upper = [
        pool for pool in pools.values()
        if not pool.consumed
        and timestamp_ns > pool.confirmed_ts_ns
        and pool.side == "UPPER"
        and previous_close <= pool.trade_level
        and float(row["high"]) >= pool.trade_level
    ]
    lower = [
        pool for pool in pools.values()
        if not pool.consumed
        and timestamp_ns > pool.confirmed_ts_ns
        and pool.side == "LOWER"
        and previous_close >= pool.trade_level
        and float(row["low"]) <= pool.trade_level
    ]
    upper.sort(key=lambda pool: pool.trade_level)
    lower.sort(key=lambda pool: pool.trade_level, reverse=True)
    return upper, lower


def _consume_target_registry(
    pools: Mapping[str, Pool],
    row: pd.Series,
    previous_close: float,
    timestamp_ns: int,
) -> None:
    for pool in pools.values():
        if pool.consumed or timestamp_ns <= int(pool.confirmed_ts_ns):
            continue
        crossed = (
            pool.side == "UPPER"
            and previous_close <= pool.level
            and float(row["high"]) >= pool.level
        ) or (
            pool.side == "LOWER"
            and previous_close >= pool.level
            and float(row["low"]) <= pool.level
        )
        if crossed:
            pool.consumed = True
            pool.consumed_ts_ns = timestamp_ns


def _nearest_target(
    registries: tuple[tuple[str, Mapping[str, Pool]], ...],
    *,
    direction: str,
    entry: float,
    risk: float,
    minimum_rr: float,
) -> tuple[str, str, float, float] | None:
    side = "UPPER" if direction == "LONG" else "LOWER"
    for label, registry in registries:
        candidates = [
            pool for pool in registry.values()
            if not pool.consumed
            and pool.side == side
            and (pool.level > entry if direction == "LONG" else pool.level < entry)
        ]
        candidates.sort(key=lambda pool: abs(pool.level - entry))
        for pool in candidates:
            rr = abs(pool.level - entry) / risk
            if rr >= minimum_rr:
                return label, pool.pool_id, float(pool.level), float(rr)
    return None


def _qualify_contact(
    row: pd.Series,
    pool: MarkIndexPool,
    *,
    logic: OvershootLogic,
) -> dict[str, float | str] | None:
    trade_atr = float(row["atr"])
    mark_atr = float(row["mark_atr"])
    index_atr = float(row["index_atr"])
    if pool.side == "UPPER":
        trade_pen = (float(row["high"]) - pool.trade_level) / trade_atr
        mark_pen = max(0.0, (float(row["mark_high"]) - pool.mark_level) / mark_atr)
        index_pen = max(0.0, (float(row["index_high"]) - pool.index_level) / index_atr)
        directional_premium_change = float(row["premium_change"])
        index_inside = float(row["index_close"]) <= pool.index_level
        attack_direction = "LONG"
        trade_direction = "SHORT"
    else:
        trade_pen = (pool.trade_level - float(row["low"])) / trade_atr
        mark_pen = max(0.0, (pool.mark_level - float(row["mark_low"])) / mark_atr)
        index_pen = max(0.0, (pool.index_level - float(row["index_low"])) / index_atr)
        directional_premium_change = -float(row["premium_change"])
        index_inside = float(row["index_close"]) >= pool.index_level
        attack_direction = "SHORT"
        trade_direction = "LONG"
    if not logic.contact_min_atr <= trade_pen <= logic.contact_max_atr:
        return None
    mark_ratio = mark_pen / max(trade_pen, 1e-12)
    index_ratio = index_pen / max(trade_pen, 1e-12)
    if not (
        mark_ratio >= logic.minimum_mark_transfer_ratio
        and index_ratio <= logic.maximum_index_transfer_ratio
        and index_inside
        and directional_premium_change > 0.0
        and float(row["premium_change_rank"]) >= logic.minimum_premium_change_rank
    ):
        return None
    return {
        "attack_direction": attack_direction,
        "trade_direction": trade_direction,
        "trade_penetration_atr": trade_pen,
        "mark_penetration_atr": mark_pen,
        "index_penetration_atr": index_pen,
        "mark_transfer_ratio": mark_ratio,
        "index_transfer_ratio": index_ratio,
        "premium_change_rank": float(row["premium_change_rank"]),
        "directional_premium_change_bps": directional_premium_change * 10_000.0,
    }


def _advance(
    bars: pd.DataFrame,
    one_targets: Mapping[str, Pool],
    five_targets: Mapping[str, Pool],
    fifteen_targets: Mapping[str, Pool],
    *,
    episode: OvershootEpisode,
    index: int,
    logic: OvershootLogic,
) -> tuple[dict[str, Any] | None, bool, int]:
    row = bars.loc[index]
    age = index - episode.contact_index
    if not bool(row["positioning_valid"]):
        return {
            "scenario_id": episode.scenario_id,
            "outcome": "POSITIONING_GAP_INVALIDATED",
            "contact": _payload(bars.loc[episode.contact_index]),
            "terminal": _payload(row),
        }, True, index
    if age > logic.confirmation_minutes:
        return {
            "scenario_id": episode.scenario_id,
            "outcome": "OVERSHOOT_RECLAIM_TIMEOUT",
            "contact": _payload(bars.loc[episode.contact_index]),
            "terminal": _payload(row),
        }, True, index

    if episode.side == "UPPER":
        trade_reclaimed = float(row["close"]) < episode.trade_level - logic.reclaim_buffer_atr * episode.trade_atr
        mark_reclaimed = float(row["mark_close"]) < episode.mark_level
        index_inside = float(row["index_close"]) <= episode.index_level
        premium_contracted = float(row["mark_index_premium"]) < episode.contact_premium
    else:
        trade_reclaimed = float(row["close"]) > episode.trade_level + logic.reclaim_buffer_atr * episode.trade_atr
        mark_reclaimed = float(row["mark_close"]) > episode.mark_level
        index_inside = float(row["index_close"]) >= episode.index_level
        premium_contracted = float(row["mark_index_premium"]) > episode.contact_premium
    body_ok = abs(float(row["close"]) - float(row["open"])) >= logic.confirmation_body_atr * episode.trade_atr
    confirmed = (
        trade_reclaimed
        and mark_reclaimed
        and index_inside
        and premium_contracted
        and body_ok
        and _directional_body(row, episode.trade_direction)
        and _same_direction_flow(row, episode.trade_direction, logic.confirmation_imbalance)
    )
    invalid = not index_inside and not confirmed
    if invalid:
        return {
            "scenario_id": episode.scenario_id,
            "outcome": "INDEX_TRANSFER_INVALIDATED_OVERSHOOT",
            "contact": _payload(bars.loc[episode.contact_index]),
            "terminal": _payload(row),
        }, True, index
    if not confirmed:
        return None, False, index

    contact = bars.loc[episode.contact_index]
    entry = float(row["close"])
    if episode.trade_direction == "LONG":
        stop = min(float(contact["low"]), float(row["low"]), episode.trade_level) - logic.stop_buffer_atr * episode.trade_atr
        risk = entry - stop
    else:
        stop = max(float(contact["high"]), float(row["high"]), episode.trade_level) + logic.stop_buffer_atr * episode.trade_atr
        risk = stop - entry
    base = {
        "scenario_id": episode.scenario_id,
        "route": "MARK_CONFIRMED_FUTURES_OVERSHOOT_REVERSAL",
        "direction": episode.trade_direction,
        "pool_id": episode.pool_id,
        "pool_side": episode.side,
        "trade_level": episode.trade_level,
        "mark_level": episode.mark_level,
        "index_level": episode.index_level,
        "contact": _payload(contact),
        "confirmation": _payload(row),
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "risk_atr": risk / episode.trade_atr,
        "confirmation_age_minutes": age,
        "trade_penetration_atr": episode.trade_penetration_atr,
        "mark_penetration_atr": episode.mark_penetration_atr,
        "index_penetration_atr": episode.index_penetration_atr,
        "mark_transfer_ratio": episode.mark_transfer_ratio,
        "index_transfer_ratio": episode.index_transfer_ratio,
        "premium_change_rank": episode.premium_change_rank,
        "directional_premium_change_bps": episode.directional_premium_change_bps,
    }
    if risk <= 0.0:
        return {**base, "outcome": "NONPOSITIVE_RISK"}, True, index
    target = _nearest_target(
        (
            ("INTERNAL_1M", one_targets),
            ("INTERNAL_5M", five_targets),
            ("EXTERNAL_15M", fifteen_targets),
        ),
        direction=episode.trade_direction,
        entry=entry,
        risk=risk,
        minimum_rr=logic.minimum_rr,
    )
    if target is None:
        return {**base, "outcome": "NO_CAUSAL_LIQUIDITY_TARGET_AT_MINIMUM_RR"}, True, index
    target_class, target_pool_id, target_price, expected_rr = target
    path, block_until = _exit_safe_path_result(
        bars,
        start_index=index,
        direction=episode.trade_direction,
        entry=entry,
        stop=stop,
        target=target_price,
        max_hold_bars=logic.max_hold_minutes,
    )
    return {
        **base,
        "outcome": "ENTRY_READY",
        "target_class": target_class,
        "target_pool_id": target_pool_id,
        "target": target_price,
        "expected_rr": expected_rr,
        "path": path,
    }, True, block_until


def diagnose(
    bars: pd.DataFrame,
    *,
    contact_confirmations: Mapping[int, list[MarkIndexPool]],
    one_target_confirmations: Mapping[int, list[Pool]],
    five_target_confirmations: Mapping[int, list[Pool]],
    fifteen_target_confirmations: Mapping[int, list[Pool]],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: OvershootLogic,
) -> dict[str, Any]:
    contact_pools: dict[str, MarkIndexPool] = {}
    one_targets: dict[str, Pool] = {}
    five_targets: dict[str, Pool] = {}
    fifteen_targets: dict[str, Pool] = {}
    episode: OvershootEpisode | None = None
    block_until = -1
    contacts: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []

    index = 1
    while index < len(bars.index):
        row = bars.loc[index]
        timestamp_ns = int(row["timestamp_ns"])
        for pool in contact_confirmations.get(timestamp_ns, []):
            contact_pools[pool.pool_id] = pool
        for pool in one_target_confirmations.get(timestamp_ns, []):
            copied = _copy_pool(pool, "T1")
            one_targets[copied.pool_id] = copied
        for pool in five_target_confirmations.get(timestamp_ns, []):
            copied = _copy_pool(pool, "T5")
            five_targets[copied.pool_id] = copied
        for pool in fifteen_target_confirmations.get(timestamp_ns, []):
            copied = _copy_pool(pool, "T15")
            fifteen_targets[copied.pool_id] = copied

        previous_close = float(bars.loc[index - 1]["close"])
        _consume_target_registry(one_targets, row, previous_close, timestamp_ns)
        _consume_target_registry(five_targets, row, previous_close, timestamp_ns)
        _consume_target_registry(fifteen_targets, row, previous_close, timestamp_ns)
        upper, lower = _contacts(contact_pools, row, previous_close, timestamp_ns)

        if index <= block_until:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            index += 1
            continue

        if episode is not None:
            record, terminal, new_block = _advance(
                bars,
                one_targets,
                five_targets,
                fifteen_targets,
                episode=episode,
                index=index,
                logic=logic,
            )
            if record is not None:
                scenarios.append(record)
            if terminal:
                episode = None
                block_until = max(block_until, new_block)
                index += 1
                continue

        if episode is not None:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            index += 1
            continue
        if upper and lower:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            contacts["AMBIGUOUS_BOTH_SIDES"] += 1
            index += 1
            continue
        touched = upper or lower
        if not touched:
            index += 1
            continue
        pool = touched[0]
        for crossed in touched:
            crossed.consumed = True
            crossed.consumed_ts_ns = timestamp_ns

        if not trade_start_ns <= timestamp_ns < trade_end_ns:
            contacts["OUTSIDE_TRADE_INTERVAL"] += 1
            index += 1
            continue
        if any(pd.isna(row[name]) or float(row[name]) <= 0.0 for name in ("atr", "mark_atr", "index_atr")):
            contacts["NO_REFERENCE_ATR"] += 1
            index += 1
            continue
        if not bool(row["positioning_valid"]):
            contacts["POSITIONING_INVALID"] += 1
            index += 1
            continue
        if pd.isna(row["premium_change_rank"]):
            contacts["PREMIUM_RANK_WARMUP"] += 1
            index += 1
            continue
        if str(row["inventory_state"]) != "RELEASE":
            contacts[f"CONTACT_{str(row['inventory_state'])}"] += 1
            index += 1
            continue
        attack_direction = "LONG" if pool.side == "UPPER" else "SHORT"
        if not (
            _same_direction_flow(row, attack_direction, logic.attack_imbalance)
            and float(row["flow_z"]) >= logic.attack_flow_z
        ):
            contacts["RELEASE_WITHOUT_ATTACK_FLOW"] += 1
            index += 1
            continue
        qualified = _qualify_contact(row, pool, logic=logic)
        if qualified is None:
            contacts["MARK_INDEX_TRANSFER_NOT_OVERSHOOT"] += 1
            index += 1
            continue
        contacts["MARK_CONFIRMED_FUTURES_OVERSHOOT"] += 1
        episode = OvershootEpisode(
            scenario_id=f"c07mio-{timestamp_ns}-{pool.pool_id}",
            contact_index=index,
            pool_id=pool.pool_id,
            side=pool.side,
            trade_level=pool.trade_level,
            mark_level=pool.mark_level,
            index_level=pool.index_level,
            trade_direction=str(qualified["trade_direction"]),
            contact_trade_extreme=float(row["high"]) if pool.side == "UPPER" else float(row["low"]),
            contact_mark_extreme=float(row["mark_high"]) if pool.side == "UPPER" else float(row["mark_low"]),
            contact_premium=float(row["mark_index_premium"]),
            contact_oi=float(row["sum_open_interest"]),
            trade_atr=float(row["atr"]),
            mark_atr=float(row["mark_atr"]),
            index_atr=float(row["index_atr"]),
            trade_penetration_atr=float(qualified["trade_penetration_atr"]),
            mark_penetration_atr=float(qualified["mark_penetration_atr"]),
            index_penetration_atr=float(qualified["index_penetration_atr"]),
            mark_transfer_ratio=float(qualified["mark_transfer_ratio"]),
            index_transfer_ratio=float(qualified["index_transfer_ratio"]),
            premium_change_rank=float(qualified["premium_change_rank"]),
            directional_premium_change_bps=float(qualified["directional_premium_change_bps"]),
        )
        index += 1

    if episode is not None:
        scenarios.append(
            {
                "scenario_id": episode.scenario_id,
                "outcome": "END_OF_DATA_WITH_ACTIVE_EPISODE",
                "contact": _payload(bars.loc[episode.contact_index]),
            }
        )

    entries = [item for item in scenarios if item.get("outcome") == "ENTRY_READY"]
    outcomes = Counter(str((item.get("path") or {}).get("outcome")) for item in entries)
    dates = {str(item["confirmation"]["timestamp"])[:10] for item in entries}
    targets = Counter(str(item.get("target_class")) for item in entries)
    mfe = [float(item["path"]["mfe_r"]) for item in entries if item["path"].get("mfe_r") is not None]
    mae = [float(item["path"]["mae_r"]) for item in entries if item["path"].get("mae_r") is not None]
    gate = {
        "minimum_entry_ready": len(entries) >= 7,
        "minimum_active_days": len(dates) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "median_mfe_at_least_minimum_rr": bool(mfe) and float(pd.Series(mfe).median()) >= logic.minimum_rr,
        "median_mae_below_one_r": bool(mae) and float(pd.Series(mae).median()) < 1.0,
    }
    gate["passed"] = all(gate.values())
    return {
        "summary": {
            "contact_counts": dict(sorted(contacts.items())),
            "scenarios": len(scenarios),
            "entry_ready": len(entries),
            "active_days": len(dates),
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "target_class_counts": dict(sorted(targets.items())),
            "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
            "median_mae_r": float(pd.Series(mae).median()) if mae else None,
            "diagnostic_gate": gate,
        },
        "scenarios": scenarios,
    }


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    logic = OvershootLogic()
    logic.validate()
    bundle = load_mark_index_positioning_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("mark_index_overshoot_data_manifest.json"),
    )
    minute = minute_frame(bundle, logic)
    states = positioning_states(
        bundle.metrics,
        oi_period=logic.oi_period,
        oi_impulse_rank=logic.oi_impulse_rank,
    )
    aligned = align_positioning_asof(minute, states)

    trade_five = five_minute_bars(bundle.frame, logic.minute_flow_period)
    mark_five = _aggregate_reference(bundle.mark_frame, 5, "mark")
    index_five = _aggregate_reference(bundle.index_frame, 5, "index")
    contacts = mark_index_pool_confirmations(
        trade_five,
        mark_five,
        index_five,
        radius=logic.contact_pivot_radius,
    )
    one_targets = pivot_confirmations(
        aligned,
        radius=logic.one_minute_target_radius,
        prefix="1",
    )
    five_targets = pivot_confirmations(
        trade_five,
        radius=logic.five_minute_target_radius,
        prefix="5",
    )
    fifteen_targets = pool_confirmations(context_bars(bundle.frame))
    result = diagnose(
        aligned,
        contact_confirmations=contacts,
        one_target_confirmations=one_targets,
        five_target_confirmations=five_targets,
        fifteen_target_confirmations=fifteen_targets,
        trade_start_ns=_utc_ns(args.start),
        trade_end_ns=_utc_ns(args.end),
        logic=logic,
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "hypothesis": "mark-confirmed futures liquidation overshoot at five-minute liquidity pools",
        "period": {"start": args.start.isoformat(), "end_exclusive": args.end.isoformat()},
        "logic": {name: getattr(logic, name) for name in logic.__dataclass_fields__},
        "data_contract": {
            "trade_mark_index": "checksum-verified exactly aligned completed one-minute Binance USD-M bars",
            "positioning": "latest completed public five-minute snapshot joined backward-as-of; invalid snapshots break state",
            "contact_pool": "five-minute traded-price swing confirmed after two completed right-side bars",
            "mapped_references": "same completed pivot bar mark and index high/low fixed at confirmation",
            "pool_activation": "strictly after confirmation timestamp",
            "route": "reversal only; trade and mark penetrate while index does not",
            "target_hierarchy": "confirmed one-minute, then five-minute, then fifteen-minute traded-price pools",
            "pool_reuse": False,
            "single_pending_or_open_slot": True,
            "future_information": False,
            "orders_or_pnl": False,
        },
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
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
