"""Pure volume-time failed-auction event detector.

This module deliberately creates no target, position path, trade slot, order,
fill, PnL or NAV.  It separates the pattern detector from every trading
scenario: a contact is blocked only while its own attack/recovery observation is
still being formed.  An old target's hypothetical holding period can therefore
never suppress a later independent market event.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
from diagnose_aggtrade_volume_time import (
    VolumeTimeLogic,
    _continuous_end,
    _payload,
    _same_second_contact_candidates,
)


def detect(
    bars: pd.DataFrame,
    *,
    source_pools: Iterable[impact.Pool],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: VolumeTimeLogic,
    require_oi_release: bool,
) -> dict[str, Any]:
    """Return accepted auction events without attaching a trade outcome."""
    logic.validate()
    work = bars.copy()
    work["positioning_valid"] = (
        work["positioning_valid"].fillna(False).astype(bool)
    )
    work["inventory_state"] = (
        work["inventory_state"].fillna("INVALID").astype(str)
    )
    contacts, collision_summary = _same_second_contact_candidates(
        work,
        source_pools,
    )
    timestamps = work["timestamp_ns"].astype("int64").to_numpy()

    counters: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    observation_end = -1

    for contact_index, pool in contacts:
        timestamp_ns = int(timestamps[contact_index])
        if not trade_start_ns <= timestamp_ns < trade_end_ns:
            counters["CONTACT_OUTSIDE_TRADE_INTERVAL"] += 1
            continue
        if contact_index <= observation_end:
            counters["CONTACT_DURING_ACTIVE_DETECTION"] += 1
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
            (attack_buy - attack_sell) / attack_total
            if attack_total > 0.0
            else 0.0
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
            "liquidity_level": float(pool.level),
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
        scenario_id = f"c07det-{timestamp_ns}-{pool.pool_id}"
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
        terminal_imbalance = 0.0
        terminal_body_atr = 0.0
        budget_exhausted = False
        for index in range(recovery_start, recovery_end_exclusive):
            row = work.iloc[index]
            buy = float(row["taker_buy_quote"])
            sell = float(row["taker_sell_quote"])
            opposite_quote += sell if direction == "SHORT" else buy
            if opposite_quote > attack_quote:
                budget_exhausted = True
                observation_end = max(observation_end, index)
                break

            reclaimed = (
                float(row["close"])
                < pool.level - logic.reclaim_buffer_atr * atr
                if direction == "SHORT"
                else float(row["close"])
                > pool.level + logic.reclaim_buffer_atr * atr
            )
            observations = index - recovery_start + 1
            if observations < logic.terminal_seconds:
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

        event = work.iloc[contact_index : recovery_end + 1]
        entry_reference = float(work.iloc[recovery_end]["close"])
        if direction == "SHORT":
            event_extreme = float(event["high"].max())
            stop = event_extreme + logic.stop_buffer_atr * atr
            risk = stop - entry_reference
            recovery_distance = event_extreme - entry_reference
        else:
            event_extreme = float(event["low"].min())
            stop = event_extreme - logic.stop_buffer_atr * atr
            risk = entry_reference - stop
            recovery_distance = entry_reference - event_extreme
        if risk <= 0.0:
            counters["NONPOSITIVE_RISK"] += 1
            continue
        recovery_quote_ratio = opposite_quote / attack_quote
        attack_impact_per_quote = penetration / max(attack_quote, 1e-12)
        recovery_impact_per_quote = recovery_distance / max(
            opposite_quote,
            1e-12,
        )
        impact_asymmetry = recovery_impact_per_quote / max(
            attack_impact_per_quote,
            1e-18,
        )
        counters["EVENT_ACCEPTED"] += 1
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "outcome": "EVENT_ACCEPTED",
                "recovery_terminal": _payload(work.iloc[recovery_end]),
                "recovery_seconds": recovery_end - recovery_start + 1,
                "opposite_quote": opposite_quote,
                "recovery_quote_ratio": recovery_quote_ratio,
                "terminal_imbalance": terminal_imbalance,
                "terminal_body_atr": terminal_body_atr,
                "impact_asymmetry": impact_asymmetry,
                "event_extreme": event_extreme,
                "entry_reference": entry_reference,
                "stop": stop,
                "risk": risk,
                **base_payload,
            }
        )

    accepted = [
        item for item in scenarios if item.get("outcome") == "EVENT_ACCEPTED"
    ]
    dates = Counter(
        pd.to_datetime(
            int(item["contact"]["timestamp_ns"]), unit="ns", utc=True
        ).date().isoformat()
        for item in accepted
    )
    return {
        "summary": {
            "contact_summary": collision_summary,
            "contact_counts": dict(sorted(counters.items())),
            "accepted_events": len(accepted),
            "active_days": len(dates),
            "events_by_day": dict(sorted(dates.items())),
            "maximum_day_share": (
                max(dates.values()) / len(accepted) if accepted else None
            ),
            "detector_has_trade_target": False,
            "detector_has_position_slot": False,
        },
        "scenarios": scenarios,
    }


__all__ = ["detect"]
