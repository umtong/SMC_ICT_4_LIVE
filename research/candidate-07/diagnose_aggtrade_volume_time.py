#!/usr/bin/env python3
"""Diagnose liquidity impact and recovery in aggressor-volume time.

This is an independent structural candidate, not a parameter relaxation of the
fixed fifteen-second model and not a backtest engine.  At the literal first
post-confirmation contact with a five-minute pool:

1. the attack leg remains active until attack-side quote flow reaches the p90
   amount from prior complete fifteen-second windows (maximum thirty seconds);
2. price progress per normalized attack quote must be low despite directional
   aggression;
3. the recovery leg receives at most the same opposite-side quote budget as the
   attack leg and must reclaim the pool with a complete terminal opposite-flow
   window before that budget is exhausted (maximum thirty seconds);
4. the entire observed event extreme defines invalidation, while targets are
   already-confirmed, unconsumed one-minute then five-minute pools.

A full reclaim using no more opposite quote than the attack quote is a direct
impact-asymmetry statement: less/equal counter-aggression undoes the attack's
price discovery.  No orders, fills, fees, PnL, cash ledger or NAV are created.
A structural pass is only permission to implement the route in NautilusTrader.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
from run_aggtrade_resilience_second_safe import (
    first_touch_after_complete_confirmation_second,
    target_pool_after_complete_confirmation_second,
)


@dataclass(frozen=True, slots=True)
class VolumeTimeLogic:
    maximum_attack_seconds: int = 30
    maximum_recovery_seconds: int = 30
    minimum_attack_imbalance: float = 0.08
    minimum_penetration_atr: float = 0.03
    maximum_penetration_atr: float = 1.50
    maximum_impact_per_flow: float = 0.35
    maximum_attack_path_efficiency: float = 0.45
    reclaim_buffer_atr: float = 0.01
    minimum_terminal_opposite_imbalance: float = 0.05
    minimum_terminal_body_atr: float = 0.01
    terminal_seconds: int = 3
    stop_buffer_atr: float = 0.05
    minimum_rr: float = 1.25

    def validate(self) -> None:
        for name in (
            "maximum_attack_seconds",
            "maximum_recovery_seconds",
            "terminal_seconds",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.terminal_seconds > self.maximum_recovery_seconds:
            raise ValueError("terminal window cannot exceed recovery horizon")
        if not 0.0 < self.minimum_attack_imbalance < 1.0:
            raise ValueError("minimum_attack_imbalance must be in (0, 1)")
        if not 0.0 <= self.minimum_terminal_opposite_imbalance < 1.0:
            raise ValueError("terminal imbalance must be in [0, 1)")
        if not 0.0 < self.minimum_penetration_atr < self.maximum_penetration_atr:
            raise ValueError("penetration bounds are inconsistent")
        if self.maximum_impact_per_flow <= 0.0:
            raise ValueError("maximum_impact_per_flow must be positive")
        if not 0.0 <= self.maximum_attack_path_efficiency <= 1.0:
            raise ValueError("attack path efficiency must be in [0, 1]")
        if self.reclaim_buffer_atr < 0.0 or self.stop_buffer_atr < 0.0:
            raise ValueError("buffers must be non-negative")
        if self.minimum_rr <= 0.0:
            raise ValueError("minimum_rr must be positive")


def _payload(row: pd.Series) -> dict[str, Any]:
    return {
        "timestamp_ns": int(row["timestamp_ns"]),
        "timestamp": pd.to_datetime(
            int(row["timestamp_ns"]), unit="ns", utc=True
        ).isoformat(),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "quote_volume": float(row["quote_volume"]),
        "taker_buy_quote": float(row["taker_buy_quote"]),
        "taker_sell_quote": float(row["taker_sell_quote"]),
        "signed_quote": float(row["signed_quote"]),
        "trade_count": int(row["trade_count"]),
        "atr": None if pd.isna(row["atr"]) else float(row["atr"]),
        "positioning_valid": bool(row.get("positioning_valid", False)),
        "inventory_state": str(row.get("inventory_state", "INVALID")),
        "open_interest": (
            None
            if pd.isna(row.get("sum_open_interest"))
            else float(row["sum_open_interest"])
        ),
        "oi_change_fraction": (
            None
            if pd.isna(row.get("oi_change_fraction"))
            else float(row["oi_change_fraction"])
        ),
    }


def _continuous_end(
    bars: pd.DataFrame,
    *,
    start: int,
    maximum_seconds: int,
) -> int:
    """Return exclusive end before the first missing second or horizon."""
    end = min(len(bars.index), start + maximum_seconds)
    if end - start <= 1:
        return end
    timestamps = bars.iloc[start:end]["timestamp_ns"].astype("int64").to_numpy()
    gaps = np.flatnonzero(np.diff(timestamps) != impact.NS_PER_SECOND)
    return end if len(gaps) == 0 else start + int(gaps[0]) + 1


def _same_second_contact_candidates(
    bars: pd.DataFrame,
    pools: Iterable[impact.Pool],
) -> tuple[list[tuple[int, impact.Pool]], dict[str, int]]:
    """Collapse only coincident contacts; variable episodes are blocked later."""
    pool_list = list(pools)
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    closes = bars["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]

    by_touch: dict[int, list[impact.Pool]] = defaultdict(list)
    never_touched = 0
    for pool in pool_list:
        touch = first_touch_after_complete_confirmation_second(
            pool,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
        )
        if touch is None:
            never_touched += 1
        else:
            by_touch[int(touch)].append(pool)

    output: list[tuple[int, impact.Pool]] = []
    counters: Counter[str] = Counter()
    for touch, touched in sorted(by_touch.items()):
        sides = {pool.side for pool in touched}
        if len(sides) > 1:
            counters["opposite_side_ambiguous_seconds"] += 1
            counters["opposite_side_pools_consumed"] += len(touched)
            continue
        if len(touched) > 1:
            counters["same_side_collision_seconds"] += 1
            counters["same_side_extra_pools_consumed"] += len(touched) - 1
        anchor = float(previous_close[touch])
        selected = min(touched, key=lambda pool: abs(pool.level - anchor))
        output.append((touch, selected))
    return output, {
        "source_pools": len(pool_list),
        "source_pools_never_touched": never_touched,
        "raw_touch_seconds": len(by_touch),
        "selected_same_second_contacts": len(output),
        **dict(sorted(counters.items())),
    }


def diagnose(
    bars: pd.DataFrame,
    *,
    source_pools: Iterable[impact.Pool],
    target_pools: Mapping[str, Iterable[impact.Pool]],
    trade_start_ns: int,
    trade_end_ns: int,
    max_hold_seconds: int,
    logic: VolumeTimeLogic,
    require_oi_release: bool = True,
) -> dict[str, Any]:
    logic.validate()
    work = bars.copy()
    work["positioning_valid"] = (
        work["positioning_valid"].fillna(False).astype(bool)
    )
    work["inventory_state"] = (
        work["inventory_state"].fillna("INVALID").astype(str)
    )
    contacts, collision_summary = _same_second_contact_candidates(
        work, source_pools
    )
    timestamps = work["timestamp_ns"].astype("int64").to_numpy()
    highs = work["high"].astype(float).to_numpy()
    lows = work["low"].astype(float).to_numpy()
    closes = work["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]

    counters: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    target_touch_cache: dict[str, int | None] = {}
    observation_end = -1
    slot_end = -1

    for contact_index, pool in contacts:
        timestamp_ns = int(timestamps[contact_index])
        if not trade_start_ns <= timestamp_ns < trade_end_ns:
            counters["CONTACT_OUTSIDE_TRADE_INTERVAL"] += 1
            continue
        if contact_index <= max(observation_end, slot_end):
            counters["CONTACT_DURING_ACTIVE_EVENT_OR_SLOT"] += 1
            continue
        contact = work.iloc[contact_index]
        if pd.isna(contact["atr"]) or float(contact["atr"]) <= 0.0:
            counters["NO_CAUSAL_ATR"] += 1
            continue
        if not bool(contact["positioning_valid"]):
            counters["POSITIONING_INVALID"] += 1
            continue
        inventory_state = str(contact["inventory_state"])
        if require_oi_release and inventory_state != "RELEASE":
            counters[f"CONTACT_{inventory_state}"] += 1
            continue
        reference_quote = float(
            contact["buy_q"] if pool.side == "UPPER" else contact["sell_q"]
        )
        if not np.isfinite(reference_quote) or reference_quote <= 0.0:
            counters["FLOW_REFERENCE_WARMUP"] += 1
            continue

        attack_end_exclusive = _continuous_end(
            work,
            start=contact_index,
            maximum_seconds=logic.maximum_attack_seconds,
        )
        attack_end: int | None = None
        attack_quote = 0.0
        attack_buy = 0.0
        attack_sell = 0.0
        for index in range(contact_index, attack_end_exclusive):
            row = work.iloc[index]
            buy = float(row["taker_buy_quote"])
            sell = float(row["taker_sell_quote"])
            attack_buy += buy
            attack_sell += sell
            attack_quote += buy if pool.side == "UPPER" else sell
            if attack_quote >= reference_quote:
                attack_end = index
                break
        if attack_end is None:
            observation_end = max(observation_end, attack_end_exclusive - 1)
            counters["ATTACK_FLOW_BUDGET_NOT_REACHED"] += 1
            continue

        attack = work.iloc[contact_index : attack_end + 1]
        observation_end = max(observation_end, attack_end)
        atr = float(contact["atr"])
        attack_total = attack_buy + attack_sell
        attack_imbalance = (
            (attack_buy - attack_sell) / attack_total if attack_total > 0.0 else 0.0
        )
        signed_attack_imbalance = (
            attack_imbalance if pool.side == "UPPER" else -attack_imbalance
        )
        attack_open = float(attack.iloc[0]["open"])
        attack_close = float(attack.iloc[-1]["close"])
        attack_path = np.concatenate(
            ([attack_open], attack["close"].astype(float).to_numpy())
        )
        attack_path_length = float(np.abs(np.diff(attack_path)).sum())
        attack_path_efficiency = (
            abs(attack_close - attack_open) / attack_path_length
            if attack_path_length > 0.0
            else 0.0
        )
        if pool.side == "UPPER":
            attack_extreme = float(attack["high"].max())
            penetration = attack_extreme - pool.level
            direction = "SHORT"
        else:
            attack_extreme = float(attack["low"].min())
            penetration = pool.level - attack_extreme
            direction = "LONG"
        penetration_atr = penetration / atr
        flow_multiple = attack_quote / reference_quote
        impact_per_flow = penetration_atr / max(flow_multiple, 1e-12)
        attack_conditions = {
            "attack_imbalance": (
                signed_attack_imbalance >= logic.minimum_attack_imbalance
            ),
            "penetration": (
                logic.minimum_penetration_atr
                <= penetration_atr
                <= logic.maximum_penetration_atr
            ),
            "impact_per_flow": (
                impact_per_flow <= logic.maximum_impact_per_flow
            ),
            "attack_path_efficiency": (
                attack_path_efficiency
                <= logic.maximum_attack_path_efficiency
            ),
        }
        failed_attack = [
            name for name, passed in attack_conditions.items() if not passed
        ]
        base_payload = {
            "pool_id": pool.pool_id,
            "pool_side": pool.side,
            "liquidity_level": pool.level,
            "direction": direction,
            "contact": _payload(contact),
            "attack_terminal": _payload(work.iloc[attack_end]),
            "attack_seconds": attack_end - contact_index + 1,
            "reference_quote": reference_quote,
            "attack_quote": attack_quote,
            "flow_multiple": flow_multiple,
            "attack_imbalance": signed_attack_imbalance,
            "penetration_atr": penetration_atr,
            "impact_per_flow": impact_per_flow,
            "attack_path_efficiency": attack_path_efficiency,
            "inventory_state": inventory_state,
            "attack_conditions": attack_conditions,
        }
        scenario_id = f"c07vol-{timestamp_ns}-{pool.pool_id}"
        if failed_attack:
            counters[f"REJECT_{failed_attack[0].upper()}"] += 1
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "outcome": "ATTACK_REJECTED",
                    "failed_conditions": failed_attack,
                    **base_payload,
                }
            )
            continue

        recovery_start = attack_end + 1
        recovery_end_exclusive = _continuous_end(
            work,
            start=recovery_start,
            maximum_seconds=logic.maximum_recovery_seconds,
        )
        recovery_end: int | None = None
        opposite_quote = 0.0
        recovery_buy = 0.0
        recovery_sell = 0.0
        terminal_imbalance = 0.0
        terminal_body_atr = 0.0
        budget_exhausted = False
        for index in range(recovery_start, recovery_end_exclusive):
            row = work.iloc[index]
            buy = float(row["taker_buy_quote"])
            sell = float(row["taker_sell_quote"])
            recovery_buy += buy
            recovery_sell += sell
            opposite_quote += sell if direction == "SHORT" else buy
            if opposite_quote > attack_quote:
                budget_exhausted = True
                observation_end = max(observation_end, index)
                break

            if direction == "SHORT":
                reclaimed = float(row["close"]) < (
                    pool.level - logic.reclaim_buffer_atr * atr
                )
            else:
                reclaimed = float(row["close"]) > (
                    pool.level + logic.reclaim_buffer_atr * atr
                )

            recovery_observations = index - recovery_start + 1
            if recovery_observations < logic.terminal_seconds:
                observation_end = max(observation_end, index)
                continue

            terminal_start = index - logic.terminal_seconds + 1
            terminal = work.iloc[terminal_start : index + 1]
            terminal_buy = float(terminal["taker_buy_quote"].sum())
            terminal_sell = float(terminal["taker_sell_quote"].sum())
            terminal_total = terminal_buy + terminal_sell
            terminal_imbalance = (
                (terminal_buy - terminal_sell) / terminal_total
                if terminal_total > 0.0
                else 0.0
            )
            terminal_body = (
                float(terminal.iloc[-1]["close"])
                - float(terminal.iloc[0]["open"])
            )
            terminal_body_atr = terminal_body / atr
            opposite_flow = (
                terminal_imbalance
                <= -logic.minimum_terminal_opposite_imbalance
                if direction == "SHORT"
                else terminal_imbalance
                >= logic.minimum_terminal_opposite_imbalance
            )
            opposite_body = (
                terminal_body_atr <= -logic.minimum_terminal_body_atr
                if direction == "SHORT"
                else terminal_body_atr >= logic.minimum_terminal_body_atr
            )
            if reclaimed and opposite_flow and opposite_body:
                recovery_end = index
                observation_end = max(observation_end, index)
                break

        if recovery_end is None:
            observation_end = max(
                observation_end,
                max(attack_end, recovery_end_exclusive - 1),
            )
            reason = (
                "RECOVERY_USED_MORE_OPPOSITE_QUOTE_THAN_ATTACK"
                if budget_exhausted
                else "RECOVERY_NOT_CONFIRMED_WITHIN_HORIZON"
            )
            counters[reason] += 1
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "outcome": reason,
                    "opposite_quote_observed": opposite_quote,
                    "recovery_quote_ratio": opposite_quote / attack_quote,
                    **base_payload,
                }
            )
            continue

        recovery = work.iloc[recovery_start : recovery_end + 1]
        event = work.iloc[contact_index : recovery_end + 1]
        entry = float(work.iloc[recovery_end]["close"])
        if direction == "SHORT":
            event_extreme = float(event["high"].max())
            stop = event_extreme + logic.stop_buffer_atr * atr
            risk = stop - entry
            recovery_distance = event_extreme - entry
        else:
            event_extreme = float(event["low"].min())
            stop = event_extreme - logic.stop_buffer_atr * atr
            risk = entry - stop
            recovery_distance = entry - event_extreme
        if risk <= 0.0:
            counters["NONPOSITIVE_RISK"] += 1
            continue
        recovery_quote_ratio = opposite_quote / attack_quote
        attack_impact_per_quote = penetration / max(attack_quote, 1e-12)
        recovery_impact_per_quote = recovery_distance / max(
            opposite_quote, 1e-12
        )
        impact_asymmetry = recovery_impact_per_quote / max(
            attack_impact_per_quote, 1e-18
        )
        selected = target_pool_after_complete_confirmation_second(
            target_pools,
            direction=direction,
            entry=entry,
            stop=stop,
            entry_index=recovery_end,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
            touch_cache=target_touch_cache,
            minimum_rr=logic.minimum_rr,
        )
        event_payload = {
            **base_payload,
            "recovery_terminal": _payload(work.iloc[recovery_end]),
            "recovery_seconds": recovery_end - recovery_start + 1,
            "opposite_quote": opposite_quote,
            "recovery_quote_ratio": recovery_quote_ratio,
            "terminal_imbalance": terminal_imbalance,
            "terminal_body_atr": terminal_body_atr,
            "impact_asymmetry": impact_asymmetry,
            "entry": entry,
            "stop": stop,
            "risk": risk,
        }
        if selected is None:
            counters["NO_CAUSAL_TARGET_AT_MINIMUM_RR"] += 1
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "outcome": "NO_CAUSAL_TARGET_AT_MINIMUM_RR",
                    **event_payload,
                }
            )
            continue
        target_pool, expected_rr = selected
        path, terminal_index = impact._path_result(
            work,
            start_index=recovery_end,
            direction=direction,
            entry=entry,
            stop=stop,
            target=target_pool.level,
            max_hold_seconds=max_hold_seconds,
        )
        slot_end = max(slot_end, terminal_index)
        counters["ENTRY_READY"] += 1
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "outcome": "ENTRY_READY",
                "target": target_pool.level,
                "target_pool_id": target_pool.pool_id,
                "target_timeframe": target_pool.timeframe,
                "expected_rr": expected_rr,
                "path": path,
                **event_payload,
            }
        )

    entries = [item for item in scenarios if item.get("outcome") == "ENTRY_READY"]
    outcomes = Counter(str(item["path"]["outcome"]) for item in entries)
    dates = Counter(
        pd.to_datetime(
            int(item["contact"]["timestamp_ns"]), unit="ns", utc=True
        ).date().isoformat()
        for item in entries
    )
    mfe = [float(item["path"]["mfe_r"]) for item in entries]
    mae = [float(item["path"]["mae_r"]) for item in entries]
    maximum_day_share = max(dates.values()) / len(entries) if entries else None
    gate = {
        "minimum_entry_ready": len(entries) >= 7,
        "minimum_active_days": len(dates) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "median_mfe_at_least_minimum_rr": (
            bool(mfe) and float(pd.Series(mfe).median()) >= logic.minimum_rr
        ),
        "median_mae_below_one_r": (
            bool(mae) and float(pd.Series(mae).median()) < 1.0
        ),
        "maximum_day_share_at_most_55pct": (
            maximum_day_share is not None and maximum_day_share <= 0.55
        ),
    }
    gate["passed"] = all(gate.values())
    return {
        "summary": {
            "contact_summary": collision_summary,
            "contact_counts": dict(sorted(counters.items())),
            "entry_ready": len(entries),
            "active_days": len(dates),
            "entries_by_day": dict(sorted(dates.items())),
            "maximum_day_share": maximum_day_share,
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
            "median_mae_r": float(pd.Series(mae).median()) if mae else None,
            "diagnostic_gate": gate,
        },
        "scenarios": scenarios,
    }


__all__ = ["VolumeTimeLogic", "diagnose"]
