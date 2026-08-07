#!/usr/bin/env python3
"""Diagnose failed-auction delivery back to the pre-attack value.

This is a structural successor to the discarded remote-liquidity target model.
The upstream volume-time event remains unchanged: a liquidity pool is attacked,
price progress is inefficient, and no more opposite aggression than the attack
budget reclaims the pool with terminal opposite flow.  The new economic claim
is narrower and directly testable:

    a rejected auction should first deliver back to the value from which the
    attack was launched, not necessarily to a remote one/five-minute pool.

The baseline value is the volume-weighted price of the last complete fifteen-
second bucket before contact.  The one controlled ablation removes only volume
weighting and uses that bucket's final trade price.  Both targets are fully known
before contact.  No orders, fees, PnL, cash ledger or NAV are created here; a
structural pass is only permission for immediate NautilusTrader implementation.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact


@dataclass(frozen=True, slots=True)
class PreAttackValueLogic:
    bucket_seconds: int = 15
    target_statistic: str = "vwap"

    def validate(self) -> None:
        if self.bucket_seconds <= 0:
            raise ValueError("bucket_seconds must be positive")
        if self.target_statistic not in {"vwap", "close"}:
            raise ValueError("target_statistic must be vwap or close")


def _exact_index(timestamps: np.ndarray, timestamp_ns: int) -> int:
    index = int(np.searchsorted(timestamps, int(timestamp_ns), side="left"))
    if index >= len(timestamps) or int(timestamps[index]) != int(timestamp_ns):
        raise RuntimeError(f"event timestamp absent from clock-second bars: {timestamp_ns}")
    return index


def _pre_attack_value(
    bars: pd.DataFrame,
    *,
    contact_index: int,
    logic: PreAttackValueLogic,
) -> tuple[float | None, dict[str, Any]]:
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    contact_second = int(timestamps[contact_index]) // impact.NS_PER_SECOND
    bucket_start_second = (
        contact_second // logic.bucket_seconds
    ) * logic.bucket_seconds
    prior_start_second = bucket_start_second - logic.bucket_seconds
    wall_seconds = timestamps // impact.NS_PER_SECOND
    indices = np.flatnonzero(
        (wall_seconds >= prior_start_second)
        & (wall_seconds < bucket_start_second)
    )
    if len(indices) != logic.bucket_seconds:
        return None, {
            "reason": "PRE_ATTACK_BUCKET_INCOMPLETE",
            "observations": int(len(indices)),
            "expected": logic.bucket_seconds,
        }
    window = bars.iloc[indices]
    if logic.target_statistic == "vwap":
        base_volume = float(window["volume"].sum())
        quote_volume = float(window["quote_volume"].sum())
        if base_volume <= 0.0 or quote_volume <= 0.0:
            return None, {
                "reason": "PRE_ATTACK_BUCKET_ZERO_VOLUME",
                "observations": int(len(indices)),
            }
        value = quote_volume / base_volume
    else:
        value = float(window.iloc[-1]["close"])
    return float(value), {
        "reason": "OK",
        "target_statistic": logic.target_statistic,
        "bucket_seconds": logic.bucket_seconds,
        "bucket_start_ns": int(timestamps[indices[0]]),
        "bucket_end_ns": int(timestamps[indices[-1]]),
        "base_volume": float(window["volume"].sum()),
        "quote_volume": float(window["quote_volume"].sum()),
        "value": float(value),
    }


def diagnose(
    bars: pd.DataFrame,
    *,
    upstream_report: Mapping[str, Any],
    max_hold_seconds: int,
    logic: PreAttackValueLogic,
) -> dict[str, Any]:
    """Retarget accepted upstream events to a causal pre-attack auction value."""
    logic.validate()
    work = bars.copy().reset_index(drop=True)
    timestamps = work["timestamp_ns"].astype("int64").to_numpy()
    accepted = [
        item
        for item in upstream_report.get("scenarios", ())
        if item.get("outcome")
        in {"ENTRY_READY", "NO_CAUSAL_TARGET_AT_MINIMUM_RR"}
        and item.get("recovery_terminal") is not None
        and item.get("contact") is not None
        and item.get("entry") is not None
        and item.get("stop") is not None
    ]
    accepted.sort(
        key=lambda item: (
            int(item["recovery_terminal"]["timestamp_ns"]),
            str(item.get("scenario_id")),
        )
    )

    counters: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    slot_end = -1
    for source in accepted:
        entry_index = _exact_index(
            timestamps,
            int(source["recovery_terminal"]["timestamp_ns"]),
        )
        if entry_index <= slot_end:
            counters["ENTRY_DURING_ACTIVE_SLOT"] += 1
            continue
        contact_index = _exact_index(
            timestamps,
            int(source["contact"]["timestamp_ns"]),
        )
        target, target_details = _pre_attack_value(
            work,
            contact_index=contact_index,
            logic=logic,
        )
        scenario_id = f"c07value-{source['scenario_id']}"
        if target is None:
            reason = str(target_details["reason"])
            counters[reason] += 1
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "outcome": reason,
                    "source_scenario_id": source.get("scenario_id"),
                    "target_details": target_details,
                }
            )
            continue

        direction = str(source["direction"])
        entry = float(source["entry"])
        stop = float(source["stop"])
        risk = entry - stop if direction == "LONG" else stop - entry
        reward = target - entry if direction == "LONG" else entry - target
        if risk <= 0.0:
            counters["NONPOSITIVE_RISK"] += 1
            continue
        if reward <= 0.0:
            counters["PRE_ATTACK_VALUE_ALREADY_DELIVERED"] += 1
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "outcome": "PRE_ATTACK_VALUE_ALREADY_DELIVERED",
                    "source_scenario_id": source.get("scenario_id"),
                    "direction": direction,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "target_details": target_details,
                }
            )
            continue

        target_rr = reward / risk
        path, terminal_index = impact._path_result(
            work,
            start_index=entry_index,
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
            max_hold_seconds=max_hold_seconds,
        )
        slot_end = max(slot_end, terminal_index)
        counters["ENTRY_READY"] += 1
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "outcome": "ENTRY_READY",
                "source_scenario_id": source.get("scenario_id"),
                "direction": direction,
                "inventory_state": source.get("inventory_state"),
                "contact": source.get("contact"),
                "entry_observed": source.get("recovery_terminal"),
                "entry": entry,
                "stop": stop,
                "risk": risk,
                "target": target,
                "target_rr": target_rr,
                "target_details": target_details,
                "upstream_recovery_quote_ratio": source.get("recovery_quote_ratio"),
                "upstream_impact_asymmetry": source.get("impact_asymmetry"),
                "path": path,
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
    realized_r: list[float] = []
    for item in entries:
        outcome = str(item["path"]["outcome"])
        if outcome == "TARGET":
            realized_r.append(float(item["target_rr"]))
        elif outcome in {"STOP", "AMBIGUOUS_SAME_SECOND"}:
            realized_r.append(-1.0)
        else:
            realized_r.append(float(item["path"]["terminal_close_r"]))
    mfe = [float(item["path"]["mfe_r"]) for item in entries]
    mae = [float(item["path"]["mae_r"]) for item in entries]
    target_rr = [float(item["target_rr"]) for item in entries]
    maximum_day_share = max(dates.values()) / len(entries) if entries else None
    gross_structural_r = float(sum(realized_r))
    gate = {
        "minimum_entry_ready": len(entries) >= 7,
        "minimum_active_days": len(dates) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "positive_gross_structural_r": gross_structural_r > 0.0,
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
            "upstream_accepted_events": len(accepted),
            "contact_counts": dict(sorted(counters.items())),
            "entry_ready": len(entries),
            "active_days": len(dates),
            "entries_by_day": dict(sorted(dates.items())),
            "maximum_day_share": maximum_day_share,
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "gross_structural_r": gross_structural_r,
            "mean_structural_r": (
                gross_structural_r / len(entries) if entries else None
            ),
            "median_target_rr": (
                float(pd.Series(target_rr).median()) if target_rr else None
            ),
            "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
            "median_mae_r": float(pd.Series(mae).median()) if mae else None,
            "diagnostic_gate": gate,
        },
        "scenarios": scenarios,
    }


__all__ = ["PreAttackValueLogic", "diagnose"]
