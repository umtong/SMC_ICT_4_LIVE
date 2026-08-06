#!/usr/bin/env python3
"""Diagnose cross-market transfer at forced-liquidation pool contacts.

The model separates two economically different events which look similar on the
perpetual chart:

1. **Futures-only overshoot** — the perpetual penetrates a causally confirmed
   fifteen-minute swing pool with OI release and aggressive flow, while the
   contemporaneous index does not transfer the move and the perp/index basis
   expands.  A prompt perpetual reclaim with opposite flow routes reversion.
2. **Common price discovery** — perpetual and index penetrate their jointly
   formed pivot levels with limited basis distortion.  A completed joint outside
   hold with same-direction flow routes continuation.

Every perpetual pool is paired, at pool confirmation time, with the index high
or low of the same completed fifteen-minute pivot bar.  Thus index confirmation
is evaluated against a level that existed before contact, not against a future
or dynamically fitted reference.

The script is a causal alpha diagnostic only.  It creates no orders, fills,
fees, funding ledger, PnL, cash balance or NAV and is not a replacement backtest
engine.  A route is implemented with NautilusTrader only if this structural path
screen passes the frozen Week-1 gate.
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
from diagnose_failed_flow import aggregate_flow
from diagnose_inventory_handoff import _directional_body, _same_direction_flow
from diagnose_inventory_handoff_exit_safe import _exit_safe_path_result
from diagnose_inventory_pressure_continuation import (
    _consume_crossed,
    _copy_pool,
    _target,
    _utc_ns,
    five_minute_pool_confirmations,
)
from diagnose_mtf_liquidity import Pool, context_bars, pool_confirmations
from diagnose_session_handoff import _align_positioning
from smc_ict_4.manifest import write_json_atomic


@dataclass(slots=True)
class CrossPool:
    pool_id: str
    side: str
    perp_level: float
    index_level: float
    pivot_ts_ns: int
    confirmed_ts_ns: int
    consumed: bool = False
    consumed_ts_ns: int | None = None


@dataclass(slots=True)
class TransferEpisode:
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
    contact_index_extreme: float
    contact_oi: float
    atr: float
    index_atr: float
    transfer_ratio: float
    perp_penetration_atr: float
    index_penetration_atr: float
    basis_change_rank: float
    directional_basis_change_bps: float


@dataclass(frozen=True, slots=True)
class TransferLogic:
    signal_minutes: int = 5
    flow_period: int = 36
    atr_period: int = 24
    oi_period: int = 36
    oi_impulse_rank: float = 0.50
    contact_min_atr: float = 0.05
    contact_max_atr: float = 1.50
    attack_imbalance: float = 0.08
    attack_flow_z: float = 0.25
    overshoot_max_transfer_ratio: float = 0.35
    discovery_min_transfer_ratio: float = 0.65
    overshoot_min_basis_change_rank: float = 0.60
    discovery_max_basis_change_rank: float = 0.80
    confirmation_bars: int = 2
    confirmation_body_atr: float = 0.12
    confirmation_imbalance: float = 0.02
    reclaim_buffer_atr: float = 0.02
    outside_buffer_atr: float = 0.02
    stop_buffer_atr: float = 0.10
    minimum_rr: float = 1.25
    max_hold_bars: int = 24
    internal_pivot_radius: int = 2
    basis_rank_period: int = 72

    def validate(self) -> None:
        if self.signal_minutes <= 0 or self.flow_period <= 0 or self.atr_period <= 0:
            raise ValueError("signal lookbacks must be positive")
        if self.oi_period <= 0 or self.basis_rank_period <= 0:
            raise ValueError("state lookbacks must be positive")
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
        if self.confirmation_bars <= 0 or self.max_hold_bars <= 0:
            raise ValueError("bar counts must be positive")
        if self.minimum_rr <= 0.0 or self.internal_pivot_radius <= 0:
            raise ValueError("geometry parameters must be positive")


def aggregate_index(frame: pd.DataFrame, minutes: int, atr_period: int) -> pd.DataFrame:
    if minutes <= 0 or atr_period <= 0:
        raise ValueError("aggregation parameters must be positive")
    work = frame.copy()
    work["bucket"] = [index // minutes for index in range(len(work.index))]
    grouped = work.groupby("bucket", sort=True)
    bars = grouped.agg(
        index_open=("open", "first"),
        index_high=("high", "max"),
        index_low=("low", "min"),
        index_close=("close", "last"),
    )
    bars["timestamp_ns"] = grouped.apply(
        lambda part: int(part.index[-1].value),
        include_groups=False,
    )
    bars = bars.reset_index(drop=True)
    previous = bars["index_close"].shift(1)
    true_range = pd.concat(
        [
            bars["index_high"] - bars["index_low"],
            (bars["index_high"] - previous).abs(),
            (bars["index_low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["index_atr"] = true_range.shift(1).rolling(
        atr_period,
        min_periods=atr_period,
    ).mean()
    return bars


def merge_cross_market(
    perp: pd.DataFrame,
    index: pd.DataFrame,
    *,
    basis_rank_period: int,
) -> pd.DataFrame:
    if not perp["timestamp_ns"].equals(index["timestamp_ns"]):
        raise RuntimeError("perpetual and index aggregate timestamps differ")
    joined = perp.copy()
    for name in (
        "index_open",
        "index_high",
        "index_low",
        "index_close",
        "index_atr",
    ):
        joined[name] = index[name].to_numpy()
    joined["basis"] = (
        (joined["close"] - joined["index_close"]) / joined["index_close"]
    )
    joined["basis_change"] = joined["basis"].diff()
    changes = joined["basis_change"].abs().tolist()
    ranks: list[float | None] = []
    for index_value, current in enumerate(changes):
        if index_value == 0 or current != current:
            ranks.append(None)
            continue
        past = [
            float(value)
            for value in changes[max(1, index_value - basis_rank_period) : index_value]
            if value == value
        ]
        if len(past) < basis_rank_period:
            ranks.append(None)
        else:
            ranks.append(sum(value <= float(current) for value in past) / len(past))
    joined["basis_change_rank"] = ranks
    joined["timestamp"] = pd.to_datetime(
        joined["timestamp_ns"],
        unit="ns",
        utc=True,
    )
    return joined


def cross_pool_confirmations(
    perp_context: pd.DataFrame,
    index_context: pd.DataFrame,
) -> dict[int, list[CrossPool]]:
    if not perp_context["timestamp_ns"].equals(index_context["timestamp_ns"]):
        raise RuntimeError("fifteen-minute perpetual/index timestamps differ")
    index_by_ns = {
        int(row.timestamp_ns): row
        for row in index_context.itertuples(index=False)
    }
    output: dict[int, list[CrossPool]] = defaultdict(list)
    for confirmation_ns, pools in pool_confirmations(perp_context).items():
        for pool in pools:
            pivot = index_by_ns.get(int(pool.pivot_ts_ns))
            if pivot is None:
                raise RuntimeError(
                    f"missing index pivot bar for {pool.pool_id}: {pool.pivot_ts_ns}"
                )
            index_level = (
                float(pivot.index_high)
                if pool.side == "UPPER"
                else float(pivot.index_low)
            )
            output[int(confirmation_ns)].append(
                CrossPool(
                    pool_id=f"X15:{pool.pool_id}",
                    side=pool.side,
                    perp_level=float(pool.level),
                    index_level=index_level,
                    pivot_ts_ns=int(pool.pivot_ts_ns),
                    confirmed_ts_ns=int(pool.confirmed_ts_ns),
                )
            )
    return output


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
        "index_atr": (
            None if pd.isna(row["index_atr"]) else float(row["index_atr"])
        ),
        "basis": float(row["basis"]),
        "basis_bps": float(row["basis"]) * 10_000.0,
        "basis_change_bps": (
            None
            if pd.isna(row["basis_change"])
            else float(row["basis_change"]) * 10_000.0
        ),
        "basis_change_rank": (
            None
            if pd.isna(row["basis_change_rank"])
            else float(row["basis_change_rank"])
        ),
        "imbalance": float(row["imbalance"]),
        "flow_z": float(row["flow_z"]),
        "positioning_valid": bool(row["positioning_valid"]),
        "open_interest": (
            None if pd.isna(row["sum_open_interest"]) else float(row["sum_open_interest"])
        ),
        "oi_change_fraction": (
            None if pd.isna(row["oi_change_fraction"]) else float(row["oi_change_fraction"])
        ),
        "oi_impulse_rank": (
            None if pd.isna(row["oi_impulse_rank"]) else float(row["oi_impulse_rank"])
        ),
        "inventory_state": str(row["inventory_state"]),
    }


def _consume_cross_contacts(
    pools: Mapping[str, CrossPool],
    row: pd.Series,
    previous_close: float,
    timestamp_ns: int,
) -> None:
    for pool in pools.values():
        if pool.consumed:
            continue
        crossed = (
            pool.side == "UPPER"
            and previous_close <= pool.perp_level
            and float(row["high"]) >= pool.perp_level
        ) or (
            pool.side == "LOWER"
            and previous_close >= pool.perp_level
            and float(row["low"]) <= pool.perp_level
        )
        if crossed:
            pool.consumed = True
            pool.consumed_ts_ns = timestamp_ns


def _contacted(
    pools: Mapping[str, CrossPool],
    row: pd.Series,
    previous_close: float,
    *,
    minimum_penetration: float,
) -> tuple[list[CrossPool], list[CrossPool]]:
    upper = [
        pool for pool in pools.values()
        if not pool.consumed
        and pool.side == "UPPER"
        and previous_close <= pool.perp_level
        and float(row["high"]) >= pool.perp_level + minimum_penetration
    ]
    lower = [
        pool for pool in pools.values()
        if not pool.consumed
        and pool.side == "LOWER"
        and previous_close >= pool.perp_level
        and float(row["low"]) <= pool.perp_level - minimum_penetration
    ]
    upper.sort(key=lambda pool: pool.perp_level)
    lower.sort(key=lambda pool: pool.perp_level, reverse=True)
    return upper, lower


def _classify_contact(
    row: pd.Series,
    pool: CrossPool,
    *,
    logic: TransferLogic,
) -> tuple[str, dict[str, float]] | None:
    atr = float(row["atr"])
    index_atr = float(row["index_atr"])
    if atr <= 0.0 or index_atr <= 0.0:
        return None
    if pool.side == "UPPER":
        perp_penetration = (float(row["high"]) - pool.perp_level) / atr
        index_penetration = max(
            0.0,
            (float(row["index_high"]) - pool.index_level) / index_atr,
        )
        attack_direction = "LONG"
        directional_basis_change = float(row["basis_change"])
        index_close_confirmed = float(row["index_close"]) > pool.index_level
    else:
        perp_penetration = (pool.perp_level - float(row["low"])) / atr
        index_penetration = max(
            0.0,
            (pool.index_level - float(row["index_low"])) / index_atr,
        )
        attack_direction = "SHORT"
        directional_basis_change = -float(row["basis_change"])
        index_close_confirmed = float(row["index_close"]) < pool.index_level
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
            "perp_penetration_atr": perp_penetration,
            "index_penetration_atr": index_penetration,
            "transfer_ratio": transfer_ratio,
            "basis_change_rank": rank,
            "directional_basis_change_bps": directional_basis_change * 10_000.0,
            "attack_direction": attack_direction,
        },
    )


def _advance_episode(
    bars: pd.DataFrame,
    internal_targets: Mapping[str, Pool],
    external_targets: Mapping[str, Pool],
    *,
    episode: TransferEpisode,
    index: int,
    logic: TransferLogic,
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
    if age > logic.confirmation_bars:
        return {
            "scenario_id": episode.scenario_id,
            "branch": episode.branch,
            "outcome": "TRANSFER_CONFIRMATION_TIMEOUT",
            "contact": _bar_payload(bars.loc[episode.contact_index]),
            "terminal": _bar_payload(row),
        }, True, index

    body_ok = abs(float(row["close"]) - float(row["open"])) >= (
        logic.confirmation_body_atr * episode.atr
    )
    if episode.branch == "FUTURES_ONLY_OVERSHOOT":
        if episode.pool_side == "UPPER":
            perp_reclaimed = float(row["close"]) < (
                episode.perp_level - logic.reclaim_buffer_atr * episode.atr
            )
            index_still_inside = float(row["index_close"]) <= episode.index_level
        else:
            perp_reclaimed = float(row["close"]) > (
                episode.perp_level + logic.reclaim_buffer_atr * episode.atr
            )
            index_still_inside = float(row["index_close"]) >= episode.index_level
        confirmed = (
            perp_reclaimed
            and index_still_inside
            and body_ok
            and _directional_body(row, episode.trade_direction)
            and _same_direction_flow(
                row,
                episode.trade_direction,
                logic.confirmation_imbalance,
            )
        )
        invalid = not index_still_inside and (
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
            joint_reclaim = (
                float(row["close"]) < episode.perp_level
                or float(row["index_close"]) < episode.index_level
            )
        else:
            joint_outside = (
                float(row["close"]) < episode.perp_level - logic.outside_buffer_atr * episode.atr
                and float(row["index_close"]) < episode.index_level
            )
            joint_reclaim = (
                float(row["close"]) > episode.perp_level
                or float(row["index_close"]) > episode.index_level
            )
        confirmed = (
            joint_outside
            and float(row["sum_open_interest"]) <= episode.contact_oi
            and body_ok
            and _directional_body(row, episode.trade_direction)
            and _same_direction_flow(
                row,
                episode.trade_direction,
                logic.confirmation_imbalance,
            )
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
    if episode.branch == "FUTURES_ONLY_OVERSHOOT":
        if episode.trade_direction == "LONG":
            stop = min(
                episode.contact_perp_extreme,
                float(row["low"]),
                episode.perp_level,
            ) - logic.stop_buffer_atr * episode.atr
            risk = entry - stop
        else:
            stop = max(
                episode.contact_perp_extreme,
                float(row["high"]),
                episode.perp_level,
            ) + logic.stop_buffer_atr * episode.atr
            risk = stop - entry
    else:
        if episode.trade_direction == "LONG":
            stop = min(
                float(contact["low"]),
                float(row["low"]),
                episode.perp_level,
            ) - logic.stop_buffer_atr * episode.atr
            risk = entry - stop
        else:
            stop = max(
                float(contact["high"]),
                float(row["high"]),
                episode.perp_level,
            ) + logic.stop_buffer_atr * episode.atr
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
        "confirmation_age_bars": age,
        "perp_penetration_atr": episode.perp_penetration_atr,
        "index_penetration_atr": episode.index_penetration_atr,
        "transfer_ratio": episode.transfer_ratio,
        "basis_change_rank": episode.basis_change_rank,
        "directional_basis_change_bps": episode.directional_basis_change_bps,
    }
    if risk <= 0.0:
        return {**base, "outcome": "NONPOSITIVE_RISK"}, True, index
    selected = _target(
        internal_targets,
        external_targets,
        direction=episode.trade_direction,
        entry=entry,
        risk=risk,
        minimum_rr=logic.minimum_rr,
    )
    if selected is None:
        return {
            **base,
            "outcome": "NO_CAUSAL_LIQUIDITY_TARGET_AT_MINIMUM_RR",
        }, True, index
    target_class, target_pool_id, target, expected_rr = selected
    path, block_until = _exit_safe_path_result(
        bars,
        start_index=index,
        direction=episode.trade_direction,
        entry=entry,
        stop=stop,
        target=target,
        max_hold_bars=logic.max_hold_bars,
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
    internal_confirmations: Mapping[int, list[Pool]],
    external_confirmations: Mapping[int, list[Pool]],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: TransferLogic,
) -> dict[str, Any]:
    logic.validate()
    cross_contacts: dict[str, CrossPool] = {}
    internal_targets: dict[str, Pool] = {}
    external_targets: dict[str, Pool] = {}
    episode: TransferEpisode | None = None
    block_until = -1
    scenarios: list[dict[str, Any]] = []
    contacts: Counter[str] = Counter()

    index = 1
    while index < len(bars.index):
        row = bars.loc[index]
        timestamp_ns = int(row["timestamp_ns"])
        for pool in cross_confirmations.get(timestamp_ns, []):
            cross_contacts[pool.pool_id] = pool
        for pool in internal_confirmations.get(timestamp_ns, []):
            copied = _copy_pool(pool, "T5")
            internal_targets[copied.pool_id] = copied
        for pool in external_confirmations.get(timestamp_ns, []):
            copied = _copy_pool(pool, "T15")
            external_targets[copied.pool_id] = copied

        previous_close = float(bars.loc[index - 1]["close"])
        _consume_crossed(internal_targets, row, previous_close, timestamp_ns)
        _consume_crossed(external_targets, row, previous_close, timestamp_ns)
        atr_value = row["atr"]
        index_atr_value = row["index_atr"]
        atr = float(atr_value) if not pd.isna(atr_value) else 0.0
        index_atr = (
            float(index_atr_value) if not pd.isna(index_atr_value) else 0.0
        )
        upper, lower = _contacted(
            cross_contacts,
            row,
            previous_close,
            minimum_penetration=logic.contact_min_atr * atr if atr > 0.0 else 0.0,
        )

        if index <= block_until:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            index += 1
            continue

        if episode is not None:
            record, terminal, new_block = _advance_episode(
                bars,
                internal_targets,
                external_targets,
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
        if atr <= 0.0 or index_atr <= 0.0:
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
        classified = _classify_contact(row, pool, logic=logic)
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
        episode = TransferEpisode(
            scenario_id=f"c07xmt-{timestamp_ns}-{pool.pool_id}",
            branch=branch,
            contact_index=index,
            pool_id=pool.pool_id,
            pool_side=pool.side,
            perp_level=pool.perp_level,
            index_level=pool.index_level,
            attack_direction=attack_direction,
            trade_direction=trade_direction,
            contact_perp_extreme=(
                float(row["high"]) if pool.side == "UPPER" else float(row["low"])
            ),
            contact_index_extreme=(
                float(row["index_high"])
                if pool.side == "UPPER"
                else float(row["index_low"])
            ),
            contact_oi=float(row["sum_open_interest"]),
            atr=atr,
            index_atr=index_atr,
            transfer_ratio=float(details["transfer_ratio"]),
            perp_penetration_atr=float(details["perp_penetration_atr"]),
            index_penetration_atr=float(details["index_penetration_atr"]),
            basis_change_rank=float(details["basis_change_rank"]),
            directional_basis_change_bps=float(
                details["directional_basis_change_bps"]
            ),
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
    target_classes = Counter(str(item.get("target_class")) for item in entry)
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
            "target_class_counts": dict(sorted(target_classes.items())),
            "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
            "median_mae_r": float(pd.Series(mae).median()) if mae else None,
            "by_branch": {
                branch: dict(sorted(counts.items()))
                for branch, counts in sorted(branches.items())
            },
            "diagnostic_gate": gate,
        },
        "scenarios": scenarios,
    }


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    logic = TransferLogic()
    logic.validate()
    bundle = load_index_positioning_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("cross_market_transfer_data_manifest.json"),
    )
    perp_five = aggregate_flow(
        bundle.frame,
        logic.signal_minutes,
        logic.flow_period,
    )
    index_five = aggregate_index(
        bundle.index_frame,
        logic.signal_minutes,
        logic.atr_period,
    )
    cross_five = merge_cross_market(
        perp_five,
        index_five,
        basis_rank_period=logic.basis_rank_period,
    )
    aligned = _align_positioning(
        cross_five,
        bundle.metrics,
        oi_period=logic.oi_period,
        oi_impulse_rank=logic.oi_impulse_rank,
    )

    perp_fifteen = context_bars(bundle.frame)
    index_fifteen = aggregate_index(bundle.index_frame, 15, logic.atr_period)
    cross_confirmations = cross_pool_confirmations(
        perp_fifteen,
        index_fifteen,
    )
    external_confirmations = pool_confirmations(perp_fifteen)
    internal_confirmations = five_minute_pool_confirmations(
        aligned,
        radius=logic.internal_pivot_radius,
    )
    result = diagnose(
        aligned,
        cross_confirmations=cross_confirmations,
        internal_confirmations=internal_confirmations,
        external_confirmations=external_confirmations,
        trade_start_ns=_utc_ns(args.start),
        trade_end_ns=_utc_ns(args.end),
        logic=logic,
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "hypothesis": "cross-market liquidation transfer at causal 15-minute liquidity pools",
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "logic": {
            name: getattr(logic, name)
            for name in logic.__dataclass_fields__
        },
        "data_contract": {
            "perpetual": "checksum-verified Binance USD-M one-minute trade bars and taker-buy volume",
            "index": "checksum-verified Binance USD-M indexPriceKlines with exact completed-minute alignment",
            "positioning": "completed public five-minute OI metrics; gaps invalidate state",
            "contact_pool": "perpetual 15-minute swing confirmed after two completed right-side bars",
            "index_pool": "same pivot bar index high/low fixed at perpetual pool confirmation",
            "target_hierarchy": "confirmed five-minute internal then fifteen-minute external perpetual pools",
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
