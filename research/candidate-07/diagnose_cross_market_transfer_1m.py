#!/usr/bin/env python3
"""One-minute cross-market liquidation-transfer diagnostic.

Fifteen-minute perpetual/index pivot levels are formed causally, but contact and
transfer are measured on completed one-minute bars so a short-lived futures-only
basis shock is not erased by five-minute aggregation.  The latest completed
public five-minute OI snapshot is joined backward-as-of at each one-minute
decision time; invalid snapshots break state and are never filled or
interpolated.

Routes:

- FUTURES_ONLY_OVERSHOOT: OI-release attack penetrates the perpetual pool, index
  transfer is weak and attack-direction basis expands; a prompt perpetual
  reclaim with opposite aggressor displacement enters reversal.
- COMMON_PRICE_DISCOVERY: index confirms the mapped pivot level with limited
  basis distortion; a joint outside hold with same-direction displacement enters
  continuation.

The pattern detector, scenario state and path diagnostic are separate.  This
script creates no orders, fills, fees, funding, PnL, cash ledger or NAV.  A route
is promoted to a NautilusTrader strategy only after passing the frozen Week-1
structural gate.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from data_index_reference import load_index_positioning_bundle
from diagnose_cross_market_liquidation_transfer import (
    CrossPool,
    aggregate_index,
    cross_pool_confirmations,
)
from diagnose_inventory_handoff import _directional_body, _same_direction_flow
from diagnose_inventory_handoff_exit_safe import _exit_safe_path_result
from diagnose_inventory_pressure_continuation import _copy_pool, _target, _utc_ns
from diagnose_mtf_liquidity import Pool, context_bars, pool_confirmations
from smc_ict_4.manifest import write_json_atomic

NS_PER_MILLISECOND = 1_000_000
NS_PER_FIVE_MINUTES = 5 * 60 * 1_000_000_000


@dataclass(slots=True)
class MinuteTransferEpisode:
    scenario_id: str
    branch: str
    contact_index: int
    pool_id: str
    pool_side: str
    perp_level: float
    index_level: float
    attack_direction: str
    trade_direction: str
    contact_perp_extreme: float
    contact_oi: float
    atr: float
    index_atr: float
    transfer_ratio: float
    perp_penetration_atr: float
    index_penetration_atr: float
    basis_change_rank: float
    directional_basis_change_bps: float


@dataclass(frozen=True, slots=True)
class MinuteTransferLogic:
    minute_atr_period: int = 60
    minute_flow_period: int = 60
    basis_rank_period: int = 120
    oi_period: int = 36
    oi_impulse_rank: float = 0.50
    contact_min_atr: float = 0.10
    contact_max_atr: float = 3.00
    attack_imbalance: float = 0.08
    attack_flow_z: float = 0.25
    overshoot_max_transfer_ratio: float = 0.35
    discovery_min_transfer_ratio: float = 0.65
    overshoot_min_basis_change_rank: float = 0.60
    discovery_max_basis_change_rank: float = 0.80
    confirmation_minutes: int = 3
    confirmation_body_atr: float = 0.15
    confirmation_imbalance: float = 0.03
    reclaim_buffer_atr: float = 0.02
    outside_buffer_atr: float = 0.02
    stop_buffer_atr: float = 0.10
    minimum_rr: float = 1.25
    max_hold_minutes: int = 120
    internal_one_minute_radius: int = 2
    internal_five_minute_radius: int = 2

    def validate(self) -> None:
        for name in (
            "minute_atr_period",
            "minute_flow_period",
            "basis_rank_period",
            "oi_period",
            "confirmation_minutes",
            "max_hold_minutes",
            "internal_one_minute_radius",
            "internal_five_minute_radius",
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
        if not 0.0 <= self.overshoot_max_transfer_ratio < self.discovery_min_transfer_ratio:
            raise ValueError("cross-market transfer classes overlap")
        if not 0.0 <= self.overshoot_min_basis_change_rank <= 1.0:
            raise ValueError("overshoot basis rank must be in [0, 1]")
        if not 0.0 <= self.discovery_max_basis_change_rank <= 1.0:
            raise ValueError("discovery basis rank must be in [0, 1]")
        if self.minimum_rr <= 0.0:
            raise ValueError("minimum_rr must be positive")


def minute_market_frame(
    perp_frame: pd.DataFrame,
    index_frame: pd.DataFrame,
    *,
    atr_period: int,
    flow_period: int,
    basis_rank_period: int,
) -> pd.DataFrame:
    if not perp_frame.index.equals(index_frame.index):
        raise RuntimeError("perpetual and index completed-minute indexes differ")
    bars = pd.DataFrame(index=perp_frame.index.copy())
    for name in ("open", "high", "low", "close", "volume", "taker_buy_base"):
        bars[name] = perp_frame[name].to_numpy()
    bars["index_open"] = index_frame["open"].to_numpy()
    bars["index_high"] = index_frame["high"].to_numpy()
    bars["index_low"] = index_frame["low"].to_numpy()
    bars["index_close"] = index_frame["close"].to_numpy()
    bars["timestamp_ns"] = bars.index.view("int64")
    bars = bars.reset_index(drop=True)

    bars["delta"] = 2.0 * bars["taker_buy_base"] - bars["volume"]
    bars["imbalance"] = (
        bars["delta"] / bars["volume"].where(bars["volume"] > 0.0)
    ).fillna(0.0)
    prior_abs_delta = bars["delta"].abs().shift(1)
    flow_mean = prior_abs_delta.rolling(flow_period, min_periods=flow_period).mean()
    flow_std = prior_abs_delta.rolling(flow_period, min_periods=flow_period).std(ddof=0)
    bars["flow_z"] = (
        (bars["delta"].abs() - flow_mean) / flow_std.where(flow_std > 1e-12)
    ).fillna(0.0)

    previous = bars["close"].shift(1)
    true_range = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous).abs(),
            (bars["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["atr"] = true_range.shift(1).rolling(
        atr_period,
        min_periods=atr_period,
    ).mean()

    index_previous = bars["index_close"].shift(1)
    index_true_range = pd.concat(
        [
            bars["index_high"] - bars["index_low"],
            (bars["index_high"] - index_previous).abs(),
            (bars["index_low"] - index_previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["index_atr"] = index_true_range.shift(1).rolling(
        atr_period,
        min_periods=atr_period,
    ).mean()

    bars["basis"] = (
        (bars["close"] - bars["index_close"]) / bars["index_close"]
    )
    bars["basis_change"] = bars["basis"].diff()
    changes = bars["basis_change"].abs().tolist()
    ranks: list[float | None] = []
    for index, current in enumerate(changes):
        if index == 0 or current != current:
            ranks.append(None)
            continue
        past = [
            float(value)
            for value in changes[max(1, index - basis_rank_period) : index]
            if value == value
        ]
        ranks.append(
            None
            if len(past) < basis_rank_period
            else sum(value <= float(current) for value in past) / len(past)
        )
    bars["basis_change_rank"] = ranks
    bars["timestamp"] = pd.to_datetime(
        bars["timestamp_ns"],
        unit="ns",
        utc=True,
    )
    return bars


def positioning_states(
    metrics: pd.DataFrame,
    *,
    oi_period: int,
    oi_impulse_rank: float,
) -> pd.DataFrame:
    selected = metrics[
        ["timestamp_ns", "sum_open_interest", "sum_open_interest_value"]
    ].copy()
    selected = selected.sort_values("timestamp_ns", kind="stable").reset_index(drop=True)
    selected["positioning_valid"] = (
        selected["sum_open_interest"].notna()
        & selected["sum_open_interest_value"].notna()
        & (selected["sum_open_interest"] > 0.0)
        & (selected["sum_open_interest_value"] > 0.0)
    )
    selected["oi_change_fraction"] = pd.NA
    selected["oi_impulse_rank"] = pd.NA
    selected["inventory_state"] = "INVALID"

    prior_changes: list[float] = []
    previous_oi: float | None = None
    previous_ns: int | None = None
    for index, row in selected.iterrows():
        if not bool(row["positioning_valid"]):
            previous_oi = None
            previous_ns = None
            prior_changes.clear()
            continue
        current_oi = float(row["sum_open_interest"])
        timestamp_ns = int(row["timestamp_ns"])
        contiguous = (
            previous_oi is not None
            and previous_ns is not None
            and timestamp_ns - previous_ns == NS_PER_FIVE_MINUTES
        )
        if not contiguous:
            selected.at[index, "inventory_state"] = "WARMUP"
            previous_oi = current_oi
            previous_ns = timestamp_ns
            prior_changes.clear()
            continue
        change = (current_oi - previous_oi) / previous_oi
        magnitudes = [abs(value) for value in prior_changes[-oi_period:]]
        rank = (
            sum(value <= abs(change) for value in magnitudes) / len(magnitudes)
            if magnitudes
            else 0.0
        )
        selected.at[index, "oi_change_fraction"] = change
        selected.at[index, "oi_impulse_rank"] = rank
        if len(magnitudes) < oi_period:
            state = "WARMUP"
        elif rank < oi_impulse_rank or change == 0.0:
            state = "NEUTRAL"
        else:
            state = "BUILD" if change > 0.0 else "RELEASE"
        selected.at[index, "inventory_state"] = state
        prior_changes.append(change)
        previous_oi = current_oi
        previous_ns = timestamp_ns
    return selected.rename(columns={"timestamp_ns": "snapshot_ns"})


def align_positioning_asof(
    bars: pd.DataFrame,
    states: pd.DataFrame,
) -> pd.DataFrame:
    work = bars.copy()
    work["decision_ns"] = work["timestamp_ns"].astype("int64") + NS_PER_MILLISECOND
    joined = pd.merge_asof(
        work.sort_values("decision_ns"),
        states.sort_values("snapshot_ns"),
        left_on="decision_ns",
        right_on="snapshot_ns",
        direction="backward",
        tolerance=NS_PER_FIVE_MINUTES,
    )
    joined["snapshot_age_ns"] = joined["decision_ns"] - joined["snapshot_ns"]
    joined["positioning_valid"] = joined["positioning_valid"].fillna(False).astype(bool)
    joined["inventory_state"] = joined["inventory_state"].fillna("INVALID")
    joined["timestamp"] = pd.to_datetime(joined["timestamp_ns"], unit="ns", utc=True)
    return joined.reset_index(drop=True)


def pivot_confirmations(
    bars: pd.DataFrame,
    *,
    radius: int,
    prefix: str,
) -> dict[int, list[Pool]]:
    output: dict[int, list[Pool]] = defaultdict(list)
    for center in range(radius, len(bars.index) - radius):
        row = bars.loc[center]
        left = bars.iloc[center - radius : center]
        right = bars.iloc[center + 1 : center + radius + 1]
        confirmation_index = center + radius
        confirmation_ns = int(bars.loc[confirmation_index]["timestamp_ns"])
        pivot_ns = int(row["timestamp_ns"])
        high = float(row["high"])
        low = float(row["low"])
        if high > float(left["high"].max()) and high > float(right["high"].max()):
            output[confirmation_ns].append(
                Pool(
                    pool_id=f"{prefix}H-{pivot_ns}",
                    side="UPPER",
                    level=high,
                    pivot_ts_ns=pivot_ns,
                    confirmed_ts_ns=confirmation_ns,
                )
            )
        if low < float(left["low"].min()) and low < float(right["low"].min()):
            output[confirmation_ns].append(
                Pool(
                    pool_id=f"{prefix}L-{pivot_ns}",
                    side="LOWER",
                    level=low,
                    pivot_ts_ns=pivot_ns,
                    confirmed_ts_ns=confirmation_ns,
                )
            )
    return output


def five_minute_bars(perp_frame: pd.DataFrame, flow_period: int) -> pd.DataFrame:
    work = perp_frame.copy()
    work["bucket"] = [index // 5 for index in range(len(work.index))]
    grouped = work.groupby("bucket", sort=True)
    bars = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    bars["timestamp_ns"] = grouped.apply(
        lambda part: int(part.index[-1].value),
        include_groups=False,
    )
    return bars.reset_index(drop=True)


def _bar_payload(row: pd.Series) -> dict[str, Any]:
    return {
        "timestamp_ns": int(row["timestamp_ns"]),
        "timestamp": row["timestamp"].isoformat(),
        "perp_open": float(row["open"]),
        "perp_high": float(row["high"]),
        "perp_low": float(row["low"]),
        "perp_close": float(row["close"]),
        "index_open": float(row["index_open"]),
        "index_high": float(row["index_high"]),
        "index_low": float(row["index_low"]),
        "index_close": float(row["index_close"]),
        "perp_atr": None if pd.isna(row["atr"]) else float(row["atr"]),
        "index_atr": None if pd.isna(row["index_atr"]) else float(row["index_atr"]),
        "basis_bps": float(row["basis"]) * 10_000.0,
        "basis_change_bps": None if pd.isna(row["basis_change"]) else float(row["basis_change"]) * 10_000.0,
        "basis_change_rank": None if pd.isna(row["basis_change_rank"]) else float(row["basis_change_rank"]),
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


def _consume_targets(
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


def _raw_contacts(
    pools: Mapping[str, CrossPool],
    row: pd.Series,
    previous_close: float,
    timestamp_ns: int,
) -> tuple[list[CrossPool], list[CrossPool]]:
    upper = [
        pool for pool in pools.values()
        if not pool.consumed
        and timestamp_ns > pool.confirmed_ts_ns
        and pool.side == "UPPER"
        and previous_close <= pool.perp_level
        and float(row["high"]) >= pool.perp_level
    ]
    lower = [
        pool for pool in pools.values()
        if not pool.consumed
        and timestamp_ns > pool.confirmed_ts_ns
        and pool.side == "LOWER"
        and previous_close >= pool.perp_level
        and float(row["low"]) <= pool.perp_level
    ]
    upper.sort(key=lambda pool: pool.perp_level)
    lower.sort(key=lambda pool: pool.perp_level, reverse=True)
    return upper, lower


def _classify(
    row: pd.Series,
    pool: CrossPool,
    *,
    logic: MinuteTransferLogic,
) -> tuple[str, dict[str, float]] | None:
    atr = float(row["atr"])
    index_atr = float(row["index_atr"])
    if pool.side == "UPPER":
        perp_penetration = (float(row["high"]) - pool.perp_level) / atr
        index_penetration = max(0.0, (float(row["index_high"]) - pool.index_level) / index_atr)
        directional_basis_change = float(row["basis_change"])
        index_close_confirmed = float(row["index_close"]) > pool.index_level
        attack_direction = "LONG"
    else:
        perp_penetration = (pool.perp_level - float(row["low"])) / atr
        index_penetration = max(0.0, (pool.index_level - float(row["index_low"])) / index_atr)
        directional_basis_change = -float(row["basis_change"])
        index_close_confirmed = float(row["index_close"]) < pool.index_level
        attack_direction = "SHORT"
    if not logic.contact_min_atr <= perp_penetration <= logic.contact_max_atr:
        return None
    transfer_ratio = index_penetration / max(perp_penetration, 1e-12)
    rank = float(row["basis_change_rank"])
    overshoot = (
        transfer_ratio <= logic.overshoot_max_transfer_ratio
        and directional_basis_change > 0.0
        and rank >= logic.overshoot_min_basis_change_rank
        and not index_close_confirmed
    )
    discovery = (
        transfer_ratio >= logic.discovery_min_transfer_ratio
        and index_close_confirmed
        and rank <= logic.discovery_max_basis_change_rank
    )
    if overshoot == discovery:
        return None
    return (
        "FUTURES_ONLY_OVERSHOOT" if overshoot else "COMMON_PRICE_DISCOVERY",
        {
            "attack_direction": attack_direction,
            "perp_penetration_atr": perp_penetration,
            "index_penetration_atr": index_penetration,
            "transfer_ratio": transfer_ratio,
            "basis_change_rank": rank,
            "directional_basis_change_bps": directional_basis_change * 10_000.0,
        },
    )


def _advance(
    bars: pd.DataFrame,
    one_minute_targets: Mapping[str, Pool],
    five_minute_targets: Mapping[str, Pool],
    fifteen_minute_targets: Mapping[str, Pool],
    *,
    episode: MinuteTransferEpisode,
    index: int,
    logic: MinuteTransferLogic,
) -> tuple[dict[str, Any] | None, bool, int]:
    row = bars.loc[index]
    age = index - episode.contact_index
    if not bool(row["positioning_valid"]):
        return {
            "scenario_id": episode.scenario_id,
            "branch": episode.branch,
            "outcome": "POSITIONING_GAP_INVALIDATED",
            "contact": _bar_payload(bars.loc[episode.contact_index]),
            "terminal": _bar_payload(row),
        }, True, index
    if age > logic.confirmation_minutes:
        return {
            "scenario_id": episode.scenario_id,
            "branch": episode.branch,
            "outcome": "MINUTE_TRANSFER_TIMEOUT",
            "contact": _bar_payload(bars.loc[episode.contact_index]),
            "terminal": _bar_payload(row),
        }, True, index

    body_ok = abs(float(row["close"]) - float(row["open"])) >= logic.confirmation_body_atr * episode.atr
    if episode.branch == "FUTURES_ONLY_OVERSHOOT":
        if episode.pool_side == "UPPER":
            perp_reclaimed = float(row["close"]) < episode.perp_level - logic.reclaim_buffer_atr * episode.atr
            index_inside = float(row["index_close"]) <= episode.index_level
        else:
            perp_reclaimed = float(row["close"]) > episode.perp_level + logic.reclaim_buffer_atr * episode.atr
            index_inside = float(row["index_close"]) >= episode.index_level
        confirmed = (
            perp_reclaimed
            and index_inside
            and body_ok
            and _directional_body(row, episode.trade_direction)
            and _same_direction_flow(row, episode.trade_direction, logic.confirmation_imbalance)
        )
        invalid = not index_inside and (
            float(row["close"]) > episode.perp_level
            if episode.attack_direction == "LONG"
            else float(row["close"]) < episode.perp_level
        )
    else:
        if episode.attack_direction == "LONG":
            joint_outside = (
                float(row["close"]) > episode.perp_level + logic.outside_buffer_atr * episode.atr
                and float(row["index_close"]) > episode.index_level
            )
            joint_reclaim = float(row["close"]) < episode.perp_level or float(row["index_close"]) < episode.index_level
        else:
            joint_outside = (
                float(row["close"]) < episode.perp_level - logic.outside_buffer_atr * episode.atr
                and float(row["index_close"]) < episode.index_level
            )
            joint_reclaim = float(row["close"]) > episode.perp_level or float(row["index_close"]) > episode.index_level
        confirmed = (
            joint_outside
            and float(row["sum_open_interest"]) <= episode.contact_oi
            and body_ok
            and _directional_body(row, episode.trade_direction)
            and _same_direction_flow(row, episode.trade_direction, logic.confirmation_imbalance)
        )
        invalid = joint_reclaim

    if invalid and not confirmed:
        return {
            "scenario_id": episode.scenario_id,
            "branch": episode.branch,
            "outcome": "TRANSFER_THESIS_INVALIDATED",
            "contact": _bar_payload(bars.loc[episode.contact_index]),
            "terminal": _bar_payload(row),
        }, True, index
    if not confirmed:
        return None, False, index

    contact = bars.loc[episode.contact_index]
    entry = float(row["close"])
    if episode.trade_direction == "LONG":
        stop = min(float(contact["low"]), float(row["low"]), episode.perp_level) - logic.stop_buffer_atr * episode.atr
        risk = entry - stop
    else:
        stop = max(float(contact["high"]), float(row["high"]), episode.perp_level) + logic.stop_buffer_atr * episode.atr
        risk = stop - entry
    base = {
        "scenario_id": episode.scenario_id,
        "branch": episode.branch,
        "direction": episode.trade_direction,
        "pool_id": episode.pool_id,
        "pool_side": episode.pool_side,
        "perp_level": episode.perp_level,
        "index_level": episode.index_level,
        "contact": _bar_payload(contact),
        "confirmation": _bar_payload(row),
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "risk_atr": risk / episode.atr,
        "confirmation_age_minutes": age,
        "perp_penetration_atr": episode.perp_penetration_atr,
        "index_penetration_atr": episode.index_penetration_atr,
        "transfer_ratio": episode.transfer_ratio,
        "basis_change_rank": episode.basis_change_rank,
        "directional_basis_change_bps": episode.directional_basis_change_bps,
    }
    if risk <= 0.0:
        return {**base, "outcome": "NONPOSITIVE_RISK"}, True, index

    selected = None
    for target_class, registry in (
        ("INTERNAL_1M", one_minute_targets),
        ("INTERNAL_5M", five_minute_targets),
        ("EXTERNAL_15M", fifteen_minute_targets),
    ):
        candidate = _target(
            registry,
            {},
            direction=episode.trade_direction,
            entry=entry,
            risk=risk,
            minimum_rr=logic.minimum_rr,
        )
        if candidate is not None:
            _, target_pool_id, target, expected_rr = candidate
            selected = (target_class, target_pool_id, target, expected_rr)
            break
    if selected is None:
        return {**base, "outcome": "NO_CAUSAL_LIQUIDITY_TARGET_AT_MINIMUM_RR"}, True, index
    target_class, target_pool_id, target, expected_rr = selected
    path, block_until = _exit_safe_path_result(
        bars,
        start_index=index,
        direction=episode.trade_direction,
        entry=entry,
        stop=stop,
        target=target,
        max_hold_bars=logic.max_hold_minutes,
    )
    return {
        **base,
        "outcome": "ENTRY_READY",
        "target_class": target_class,
        "target_pool_id": target_pool_id,
        "target": target,
        "expected_rr": expected_rr,
        "path": path,
    }, True, block_until


def diagnose(
    bars: pd.DataFrame,
    *,
    cross_confirmations: Mapping[int, list[CrossPool]],
    one_minute_confirmations: Mapping[int, list[Pool]],
    five_minute_confirmations: Mapping[int, list[Pool]],
    fifteen_minute_confirmations: Mapping[int, list[Pool]],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: MinuteTransferLogic,
) -> dict[str, Any]:
    cross_contacts: dict[str, CrossPool] = {}
    one_minute_targets: dict[str, Pool] = {}
    five_minute_targets: dict[str, Pool] = {}
    fifteen_minute_targets: dict[str, Pool] = {}
    episode: MinuteTransferEpisode | None = None
    block_until = -1
    scenarios: list[dict[str, Any]] = []
    contacts: Counter[str] = Counter()

    index = 1
    while index < len(bars.index):
        row = bars.loc[index]
        timestamp_ns = int(row["timestamp_ns"])
        for pool in cross_confirmations.get(timestamp_ns, []):
            cross_contacts[pool.pool_id] = pool
        for pool in one_minute_confirmations.get(timestamp_ns, []):
            copied = _copy_pool(pool, "T1")
            one_minute_targets[copied.pool_id] = copied
        for pool in five_minute_confirmations.get(timestamp_ns, []):
            copied = _copy_pool(pool, "T5")
            five_minute_targets[copied.pool_id] = copied
        for pool in fifteen_minute_confirmations.get(timestamp_ns, []):
            copied = _copy_pool(pool, "T15")
            fifteen_minute_targets[copied.pool_id] = copied

        previous_close = float(bars.loc[index - 1]["close"])
        _consume_targets(one_minute_targets, row, previous_close, timestamp_ns)
        _consume_targets(five_minute_targets, row, previous_close, timestamp_ns)
        _consume_targets(fifteen_minute_targets, row, previous_close, timestamp_ns)
        upper, lower = _raw_contacts(cross_contacts, row, previous_close, timestamp_ns)

        if index <= block_until:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            index += 1
            continue

        if episode is not None:
            record, terminal, new_block = _advance(
                bars,
                one_minute_targets,
                five_minute_targets,
                fifteen_minute_targets,
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
        if pd.isna(row["atr"]) or pd.isna(row["index_atr"]) or float(row["atr"]) <= 0.0 or float(row["index_atr"]) <= 0.0:
            contacts["NO_CROSS_MARKET_ATR"] += 1
            index += 1
            continue
        if not bool(row["positioning_valid"]):
            contacts["POSITIONING_INVALID"] += 1
            index += 1
            continue
        if pd.isna(row["basis_change_rank"]):
            contacts["BASIS_RANK_WARMUP"] += 1
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
        classified = _classify(row, pool, logic=logic)
        if classified is None:
            contacts["CROSS_MARKET_AMBIGUOUS"] += 1
            index += 1
            continue
        branch, details = classified
        contacts[branch] += 1
        trade_direction = (
            ("SHORT" if attack_direction == "LONG" else "LONG")
            if branch == "FUTURES_ONLY_OVERSHOOT"
            else attack_direction
        )
        episode = MinuteTransferEpisode(
            scenario_id=f"c07x1m-{timestamp_ns}-{pool.pool_id}",
            branch=branch,
            contact_index=index,
            pool_id=pool.pool_id,
            pool_side=pool.side,
            perp_level=pool.perp_level,
            index_level=pool.index_level,
            attack_direction=attack_direction,
            trade_direction=trade_direction,
            contact_perp_extreme=float(row["high"]) if pool.side == "UPPER" else float(row["low"]),
            contact_oi=float(row["sum_open_interest"]),
            atr=float(row["atr"]),
            index_atr=float(row["index_atr"]),
            transfer_ratio=float(details["transfer_ratio"]),
            perp_penetration_atr=float(details["perp_penetration_atr"]),
            index_penetration_atr=float(details["index_penetration_atr"]),
            basis_change_rank=float(details["basis_change_rank"]),
            directional_basis_change_bps=float(details["directional_basis_change_bps"]),
        )
        index += 1

    if episode is not None:
        scenarios.append(
            {
                "scenario_id": episode.scenario_id,
                "branch": episode.branch,
                "outcome": "END_OF_DATA_WITH_ACTIVE_EPISODE",
                "contact": _bar_payload(bars.loc[episode.contact_index]),
            }
        )

    entry = [item for item in scenarios if item.get("outcome") == "ENTRY_READY"]
    outcomes = Counter(str((item.get("path") or {}).get("outcome")) for item in entry)
    dates = {str(item["confirmation"]["timestamp"])[:10] for item in entry}
    branches: dict[str, Counter[str]] = defaultdict(Counter)
    targets = Counter(str(item.get("target_class")) for item in entry)
    for item in scenarios:
        branches[str(item.get("branch", "UNROUTED"))][str(item.get("outcome"))] += 1
    mfe = [float(item["path"]["mfe_r"]) for item in entry if item["path"].get("mfe_r") is not None]
    mae = [float(item["path"]["mae_r"]) for item in entry if item["path"].get("mae_r") is not None]
    gate = {
        "minimum_entry_ready": len(entry) >= 7,
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
            "entry_ready": len(entry),
            "active_days": len(dates),
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "target_class_counts": dict(sorted(targets.items())),
            "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
            "median_mae_r": float(pd.Series(mae).median()) if mae else None,
            "by_branch": {branch: dict(sorted(counts.items())) for branch, counts in sorted(branches.items())},
            "diagnostic_gate": gate,
        },
        "scenarios": scenarios,
    }


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    logic = MinuteTransferLogic()
    logic.validate()
    bundle = load_index_positioning_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("cross_market_1m_data_manifest.json"),
    )
    minute = minute_market_frame(
        bundle.frame,
        bundle.index_frame,
        atr_period=logic.minute_atr_period,
        flow_period=logic.minute_flow_period,
        basis_rank_period=logic.basis_rank_period,
    )
    states = positioning_states(
        bundle.metrics,
        oi_period=logic.oi_period,
        oi_impulse_rank=logic.oi_impulse_rank,
    )
    aligned = align_positioning_asof(minute, states)

    perp_fifteen = context_bars(bundle.frame)
    index_fifteen = aggregate_index(bundle.index_frame, 15, logic.minute_atr_period)
    cross_confirmations = cross_pool_confirmations(perp_fifteen, index_fifteen)
    fifteen_targets = pool_confirmations(perp_fifteen)
    one_targets = pivot_confirmations(
        aligned,
        radius=logic.internal_one_minute_radius,
        prefix="1",
    )
    five = five_minute_bars(bundle.frame, logic.minute_flow_period)
    five_targets = pivot_confirmations(
        five,
        radius=logic.internal_five_minute_radius,
        prefix="5",
    )
    result = diagnose(
        aligned,
        cross_confirmations=cross_confirmations,
        one_minute_confirmations=one_targets,
        five_minute_confirmations=five_targets,
        fifteen_minute_confirmations=fifteen_targets,
        trade_start_ns=_utc_ns(args.start),
        trade_end_ns=_utc_ns(args.end),
        logic=logic,
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "hypothesis": "one-minute cross-market liquidation transfer with completed five-minute OI state",
        "period": {"start": args.start.isoformat(), "end_exclusive": args.end.isoformat()},
        "logic": {name: getattr(logic, name) for name in logic.__dataclass_fields__},
        "data_contract": {
            "contact_and_execution_state": "exactly aligned completed one-minute perpetual and index bars",
            "positioning": "latest completed public five-minute snapshot joined backward-as-of at bar-close plus one millisecond",
            "contact_pool": "fifteen-minute perpetual swing confirmed after two completed right-side bars",
            "mapped_index_pool": "same completed pivot bar index high/low",
            "pool_activation": "strictly after confirmation timestamp",
            "target_hierarchy": "confirmed one-minute, then five-minute, then fifteen-minute perpetual pools",
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
