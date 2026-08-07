"""Trade scenario: reclaimed liquidity retest, then return to auction value.

The pure detector supplies a completed failed-auction event.  This scenario does
not enter at the initial reclaim.  It waits for the first revisit of the swept
pool, requires that the revisit close back on the reclaimed side with a complete
three-second directional rejection, and only then targets the already-known
pre-attack fifteen-second VWAP.  The event extreme remains the invalidation.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
from diagnose_pre_attack_value import (
    PreAttackValueLogic,
    _pre_attack_value,
)


@dataclass(frozen=True, slots=True)
class ReclaimedPoolRetestLogic:
    confirmation_seconds: int = 3
    minimum_directional_imbalance: float = 0.05
    minimum_directional_body_atr: float = 0.01
    reclaimed_close_buffer_atr: float = 0.01
    target_statistic: str = "vwap"

    def validate(self) -> None:
        if self.confirmation_seconds <= 0:
            raise ValueError("confirmation_seconds must be positive")
        if not 0.0 <= self.minimum_directional_imbalance < 1.0:
            raise ValueError("directional imbalance must be in [0, 1)")
        if self.minimum_directional_body_atr < 0.0:
            raise ValueError("directional body threshold must be non-negative")
        if self.reclaimed_close_buffer_atr < 0.0:
            raise ValueError("reclaimed close buffer must be non-negative")
        if self.target_statistic not in {"vwap", "close"}:
            raise ValueError("target_statistic must be vwap or close")


def _exact_index(timestamps: np.ndarray, timestamp_ns: int) -> int:
    index = int(np.searchsorted(timestamps, int(timestamp_ns), side="left"))
    if index >= len(timestamps) or int(timestamps[index]) != int(timestamp_ns):
        raise RuntimeError(f"timestamp absent from clock-second bars: {timestamp_ns}")
    return index


def diagnose(
    bars: pd.DataFrame,
    *,
    detector_report: Mapping[str, Any],
    max_hold_seconds: int,
    logic: ReclaimedPoolRetestLogic,
    require_flow_confirmation: bool,
) -> dict[str, Any]:
    logic.validate()
    if max_hold_seconds <= 0:
        raise ValueError("max_hold_seconds must be positive")
    work = bars.copy().reset_index(drop=True)
    timestamps = work["timestamp_ns"].astype("int64").to_numpy()
    highs = work["high"].astype(float).to_numpy()
    lows = work["low"].astype(float).to_numpy()
    closes = work["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]

    accepted = [
        item
        for item in detector_report.get("scenarios", ())
        if item.get("outcome") == "EVENT_ACCEPTED"
    ]
    accepted.sort(
        key=lambda item: (
            int(item["recovery_terminal"]["timestamp_ns"]),
            str(item["scenario_id"]),
        )
    )

    counters: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    slot_end = -1
    value_logic = PreAttackValueLogic(target_statistic=logic.target_statistic)

    for source in accepted:
        recovery_index = _exact_index(
            timestamps,
            int(source["recovery_terminal"]["timestamp_ns"]),
        )
        if recovery_index <= slot_end:
            counters["EVENT_DURING_ACTIVE_POSITION_SLOT"] += 1
            continue
        contact_index = _exact_index(
            timestamps,
            int(source["contact"]["timestamp_ns"]),
        )
        target, target_details = _pre_attack_value(
            work,
            contact_index=contact_index,
            logic=value_logic,
        )
        scenario_id = f"c07retest-{source['scenario_id']}"
        if target is None:
            reason = str(target_details["reason"])
            counters[reason] += 1
            continue

        direction = str(source["direction"])
        pool_level = float(source["liquidity_level"])
        stop = float(source["stop"])
        atr = float(source["contact"]["atr"])
        start = recovery_index + 1
        end = min(len(work.index), start + max_hold_seconds)
        if start >= end:
            counters["NO_POST_EVENT_HORIZON"] += 1
            continue

        retest_index: int | None = None
        pre_entry_reason: str | None = None
        for index in range(start, end):
            if direction == "SHORT":
                stop_hit = highs[index] >= stop
                target_hit = lows[index] <= target
                retest = (
                    previous_close[index] < pool_level
                    and highs[index] >= pool_level
                )
            else:
                stop_hit = lows[index] <= stop
                target_hit = highs[index] >= target
                retest = (
                    previous_close[index] > pool_level
                    and lows[index] <= pool_level
                )
            if stop_hit and target_hit:
                pre_entry_reason = "AMBIGUOUS_TARGET_AND_STOP_BEFORE_RETEST"
                break
            if stop_hit:
                pre_entry_reason = "INVALIDATION_BEFORE_RETEST"
                break
            if target_hit:
                pre_entry_reason = "VALUE_DELIVERED_BEFORE_RETEST"
                break
            if retest:
                retest_index = index
                break

        if retest_index is None:
            reason = pre_entry_reason or "NO_RETEST_WITHIN_HORIZON"
            counters[reason] += 1
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "outcome": reason,
                    "source_scenario_id": source["scenario_id"],
                    "direction": direction,
                    "pool_level": pool_level,
                    "stop": stop,
                    "target": target,
                    "target_details": target_details,
                }
            )
            continue

        confirmation_end = retest_index + logic.confirmation_seconds
        if confirmation_end > end:
            counters["INCOMPLETE_RETEST_CONFIRMATION"] += 1
            continue
        confirmation = work.iloc[retest_index:confirmation_end]
        differences = confirmation["timestamp_ns"].astype("int64").diff().dropna()
        if bool((differences != impact.NS_PER_SECOND).any()):
            counters["SECOND_GAP_IN_RETEST_CONFIRMATION"] += 1
            continue

        if direction == "SHORT":
            stop_in_confirmation = bool((confirmation["high"] >= stop).any())
            target_in_confirmation = bool((confirmation["low"] <= target).any())
        else:
            stop_in_confirmation = bool((confirmation["low"] <= stop).any())
            target_in_confirmation = bool((confirmation["high"] >= target).any())
        if stop_in_confirmation and target_in_confirmation:
            counters["AMBIGUOUS_TARGET_AND_STOP_DURING_CONFIRMATION"] += 1
            continue
        if stop_in_confirmation:
            counters["INVALIDATION_DURING_RETEST_CONFIRMATION"] += 1
            continue
        if target_in_confirmation:
            counters["VALUE_DELIVERED_DURING_RETEST_CONFIRMATION"] += 1
            continue

        terminal = confirmation.iloc[-1]
        terminal_close = float(terminal["close"])
        buy_quote = float(confirmation["taker_buy_quote"].sum())
        sell_quote = float(confirmation["taker_sell_quote"].sum())
        total_quote = buy_quote + sell_quote
        imbalance = (
            (buy_quote - sell_quote) / total_quote
            if total_quote > 0.0
            else 0.0
        )
        body = (
            float(confirmation.iloc[-1]["close"])
            - float(confirmation.iloc[0]["open"])
        )
        body_atr = body / atr
        reclaimed_close = (
            terminal_close
            < pool_level - logic.reclaimed_close_buffer_atr * atr
            if direction == "SHORT"
            else terminal_close
            > pool_level + logic.reclaimed_close_buffer_atr * atr
        )
        directional_flow = (
            imbalance <= -logic.minimum_directional_imbalance
            if direction == "SHORT"
            else imbalance >= logic.minimum_directional_imbalance
        )
        directional_body = (
            body_atr <= -logic.minimum_directional_body_atr
            if direction == "SHORT"
            else body_atr >= logic.minimum_directional_body_atr
        )
        conditions = {
            "reclaimed_close": reclaimed_close,
            "directional_flow": (
                directional_flow if require_flow_confirmation else True
            ),
            "directional_body": directional_body,
        }
        failed = [name for name, passed in conditions.items() if not passed]
        if failed:
            reason = f"FIRST_RETEST_REJECT_{failed[0].upper()}"
            counters[reason] += 1
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "outcome": reason,
                    "failed_conditions": failed,
                    "source_scenario_id": source["scenario_id"],
                    "direction": direction,
                    "pool_level": pool_level,
                    "retest_time_ns": int(
                        work.iloc[retest_index]["timestamp_ns"]
                    ),
                    "terminal_time_ns": int(terminal["timestamp_ns"]),
                    "terminal_imbalance": imbalance,
                    "terminal_body_atr": body_atr,
                    "conditions": conditions,
                }
            )
            continue

        entry_index = confirmation_end - 1
        entry = terminal_close
        risk = entry - stop if direction == "LONG" else stop - entry
        reward = target - entry if direction == "LONG" else entry - target
        if risk <= 0.0:
            counters["NONPOSITIVE_RISK_AFTER_RETEST"] += 1
            continue
        if reward <= 0.0:
            counters["VALUE_ALREADY_DELIVERED_AT_ENTRY"] += 1
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
                "source_scenario_id": source["scenario_id"],
                "direction": direction,
                "inventory_state": source.get("inventory_state"),
                "contact": source["contact"],
                "event_recovery_terminal": source["recovery_terminal"],
                "pool_level": pool_level,
                "retest_time_ns": int(work.iloc[retest_index]["timestamp_ns"]),
                "entry_observed": {
                    "timestamp_ns": int(terminal["timestamp_ns"]),
                    "close": terminal_close,
                },
                "entry": entry,
                "stop": stop,
                "risk": risk,
                "target": target,
                "target_rr": target_rr,
                "target_details": target_details,
                "terminal_imbalance": imbalance,
                "terminal_body_atr": body_atr,
                "require_flow_confirmation": require_flow_confirmation,
                "detector_recovery_quote_ratio": source.get(
                    "recovery_quote_ratio"
                ),
                "detector_impact_asymmetry": source.get("impact_asymmetry"),
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
    mfe = []
    mae = []
    target_rr_values = []
    for item in entries:
        outcome = str(item["path"]["outcome"])
        if outcome == "TARGET":
            realized_r.append(float(item["target_rr"]))
        elif outcome in {"STOP", "AMBIGUOUS_SAME_SECOND"}:
            realized_r.append(-1.0)
        else:
            realized_r.append(float(item["path"]["terminal_close_r"]))
        mfe.append(float(item["path"]["mfe_r"]))
        mae.append(float(item["path"]["mae_r"]))
        target_rr_values.append(float(item["target_rr"]))
    gross_r = float(sum(realized_r))
    maximum_day_share = max(dates.values()) / len(entries) if entries else None
    gate = {
        "minimum_entry_ready": len(entries) >= 7,
        "minimum_active_days": len(dates) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "positive_gross_structural_r": gross_r > 0.0,
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
            "detector_accepted_events": len(accepted),
            "contact_counts": dict(sorted(counters.items())),
            "entry_ready": len(entries),
            "active_days": len(dates),
            "entries_by_day": dict(sorted(dates.items())),
            "maximum_day_share": maximum_day_share,
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "gross_structural_r": gross_r,
            "mean_structural_r": gross_r / len(entries) if entries else None,
            "median_target_rr": (
                float(pd.Series(target_rr_values).median())
                if target_rr_values
                else None
            ),
            "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
            "median_mae_r": float(pd.Series(mae).median()) if mae else None,
            "diagnostic_gate": gate,
        },
        "scenarios": scenarios,
    }


__all__ = ["ReclaimedPoolRetestLogic", "diagnose"]
