"""Value normalization -> MSS/displacement -> FVG retest structural scenario.

The pure volume-time detector supplies a completed failed-auction event. The
baseline does not trade the return to pre-attack value. It treats a completed
close through that already-known value as normalization, then requires a later
one-minute market-structure shift with ranked displacement, a causal FVG and the
first valid FVG retest. Entry is measured on the first completed aggregate-
trade second after the signal minute. The target is the nearest opposing
five-minute liquidity pool which was confirmed and remained unconsumed before
entry. If that first external objective is not positive after the declared
adverse execution costs, the event is not traded; a farther pool is not used to
bypass the first objective.

This module creates no orders, fills, cash ledger, PnL or NAV. Its controlled
ablation removes only the value-normalization milestone. Detector, MSS,
displacement, FVG, retest, stop, target hierarchy, costs and position-slot path
logic remain unchanged.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
from diagnose_impact_mss_fvg_paths import _first_second_after_completed_signal
from diagnose_pre_attack_value import PreAttackValueLogic, _pre_attack_value
from execution_cost_geometry import (
    AdverseExecutionGeometry,
    adverse_execution_geometry,
)
from model_impact_mss_fvg import (
    ImpactEvent,
    ImpactMSSFVGLogic,
    diagnose as diagnose_mss_fvg,
)
from run_aggtrade_resilience_second_safe import (
    target_pool_after_complete_confirmation_second,
)


@dataclass(frozen=True, slots=True)
class ValueNormalizedMSSFVGLogic:
    maximum_normalization_seconds: int = 300
    target_timeframe: str = "5M"
    target_statistic: str = "vwap"

    def validate(self) -> None:
        if self.maximum_normalization_seconds <= 0:
            raise ValueError("maximum_normalization_seconds must be positive")
        if self.target_timeframe != "5M":
            raise ValueError(
                "target_timeframe is frozen to opposing 5M liquidity"
            )
        if self.target_statistic not in {"vwap", "close"}:
            raise ValueError("target_statistic must be vwap or close")


def _exact_index(timestamps: np.ndarray, timestamp_ns: int) -> int:
    index = int(np.searchsorted(timestamps, int(timestamp_ns), side="left"))
    if index >= len(timestamps) or int(timestamps[index]) != int(timestamp_ns):
        raise RuntimeError(
            f"timestamp absent from clock-second bars: {timestamp_ns}"
        )
    return index


def find_value_normalization(
    bars: pd.DataFrame,
    *,
    recovery_index: int,
    direction: str,
    value: float,
    event_extreme: float,
    maximum_seconds: int,
) -> dict[str, Any]:
    """Find the first completed close through value before source invalidation."""
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported direction: {direction}")
    if maximum_seconds <= 0:
        raise ValueError("maximum_seconds must be positive")
    if not 0 <= recovery_index < len(bars.index):
        raise IndexError("recovery_index outside bars")

    end = min(len(bars.index), recovery_index + maximum_seconds + 1)
    for index in range(recovery_index, end):
        row = bars.iloc[index]
        if direction == "LONG":
            invalidated = float(row["low"]) <= event_extreme
            normalized = float(row["close"]) >= value
        else:
            invalidated = float(row["high"]) >= event_extreme
            normalized = float(row["close"]) <= value
        if invalidated and normalized:
            return {
                "outcome": "AMBIGUOUS_VALUE_AND_INVALIDATION",
                "index": index,
                "timestamp_ns": int(row["timestamp_ns"]),
                "close": float(row["close"]),
            }
        if invalidated:
            return {
                "outcome": "SOURCE_INVALIDATED_BEFORE_VALUE_NORMALIZATION",
                "index": index,
                "timestamp_ns": int(row["timestamp_ns"]),
                "close": float(row["close"]),
            }
        if normalized:
            return {
                "outcome": "VALUE_NORMALIZED",
                "index": index,
                "timestamp_ns": int(row["timestamp_ns"]),
                "close": float(row["close"]),
            }
    return {
        "outcome": "VALUE_NOT_NORMALIZED_WITHIN_WINDOW",
        "index": None,
        "timestamp_ns": None,
        "close": None,
    }


def events_from_detector(
    bars: pd.DataFrame,
    *,
    detector_report: Mapping[str, Any],
    logic: ValueNormalizedMSSFVGLogic,
    require_value_normalization: bool,
) -> tuple[
    list[ImpactEvent],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    """Convert target-free detector events into MSS/FVG start states."""
    logic.validate()
    work = (
        bars.copy()
        .sort_values("timestamp_ns", kind="stable")
        .reset_index(drop=True)
    )
    timestamps = work["timestamp_ns"].astype("int64").to_numpy()
    value_logic = PreAttackValueLogic(
        target_statistic=logic.target_statistic
    )
    value_logic.validate()

    accepted = [
        dict(item)
        for item in detector_report.get("scenarios", ())
        if item.get("outcome") == "EVENT_ACCEPTED"
    ]
    accepted.sort(
        key=lambda item: (
            int(item["recovery_terminal"]["timestamp_ns"]),
            str(item["scenario_id"]),
        )
    )

    events: list[ImpactEvent] = []
    details_by_event: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    for source in accepted:
        source_id = str(source["scenario_id"])
        contact_index = _exact_index(
            timestamps,
            int(source["contact"]["timestamp_ns"]),
        )
        recovery_index = _exact_index(
            timestamps,
            int(source["recovery_terminal"]["timestamp_ns"]),
        )
        value, value_details = _pre_attack_value(
            work,
            contact_index=contact_index,
            logic=value_logic,
        )
        if value is None:
            diagnostics.append(
                {
                    "source_scenario_id": source_id,
                    "outcome": str(value_details["reason"]),
                    "value_details": value_details,
                }
            )
            continue

        direction = str(source["direction"])
        normalization = find_value_normalization(
            work,
            recovery_index=recovery_index,
            direction=direction,
            value=float(value),
            event_extreme=float(source["event_extreme"]),
            maximum_seconds=logic.maximum_normalization_seconds,
        )
        if require_value_normalization:
            if normalization["outcome"] != "VALUE_NORMALIZED":
                diagnostics.append(
                    {
                        "source_scenario_id": source_id,
                        "outcome": normalization["outcome"],
                        "value": float(value),
                        "value_details": value_details,
                        "normalization": normalization,
                    }
                )
                continue
            event_end_ns = int(normalization["timestamp_ns"])
            suffix = "VALUE-NORMALIZED"
        else:
            event_end_ns = int(
                source["recovery_terminal"]["timestamp_ns"]
            )
            suffix = "NO-VALUE-MILESTONE"

        event_id = f"{source_id}-{suffix}"
        event = ImpactEvent(
            event_id=event_id,
            direction=direction,  # type: ignore[arg-type]
            event_end_ns=event_end_ns,
            source_pool_id=str(source["pool_id"]),
            source_level=float(source["liquidity_level"]),
            event_extreme=float(source["event_extreme"]),
        )
        events.append(event)
        details_by_event[event_id] = {
            "source_scenario_id": source_id,
            "direction": direction,
            "source_pool_id": str(source["pool_id"]),
            "source_level": float(source["liquidity_level"]),
            "event_extreme": float(source["event_extreme"]),
            "contact": source["contact"],
            "recovery_terminal": source["recovery_terminal"],
            "pre_attack_value": float(value),
            "pre_attack_value_details": value_details,
            "normalization": normalization,
            "require_value_normalization": require_value_normalization,
            "mss_search_begins_after_ns": event_end_ns,
        }
    events.sort(key=lambda item: (item.event_end_ns, item.event_id))
    return events, details_by_event, diagnostics


def _cost_adjusted_terminal_r(
    *,
    path: Mapping[str, Any],
    direction: str,
    entry: Decimal,
    stop: Decimal,
    geometry: AdverseExecutionGeometry,
    price_increment: Decimal,
    taker_fee_rate: Decimal,
) -> Decimal:
    outcome = str(path["outcome"])
    if outcome == "TARGET":
        return geometry.cost_adjusted_target_r
    if outcome in {"STOP", "AMBIGUOUS_SAME_SECOND"}:
        return Decimal("-1")

    terminal_r = Decimal(str(path["terminal_close_r"]))
    raw_risk = entry - stop if direction == "LONG" else stop - entry
    if raw_risk <= 0:
        raise ValueError("raw terminal risk must be positive")
    terminal_close = (
        entry + terminal_r * raw_risk
        if direction == "LONG"
        else entry - terminal_r * raw_risk
    )
    if direction == "LONG":
        expected_exit = terminal_close - price_increment
        gross_gain = expected_exit - geometry.expected_entry_fill
    else:
        expected_exit = terminal_close + price_increment
        gross_gain = geometry.expected_entry_fill - expected_exit
    if expected_exit <= 0:
        return Decimal("-1")
    exit_fee = expected_exit * taker_fee_rate
    net_gain = (
        gross_gain
        - geometry.entry_fee
        - exit_fee
        - geometry.funding_reserve
    )
    return net_gain / geometry.per_unit_expected_loss


def evaluate(
    minutes: pd.DataFrame,
    seconds: pd.DataFrame,
    *,
    detector_report: Mapping[str, Any],
    target_pools: Mapping[str, Iterable[impact.Pool]],
    maximum_hold_seconds: int,
    state_logic: ValueNormalizedMSSFVGLogic,
    mss_logic: ImpactMSSFVGLogic,
    require_value_normalization: bool,
    price_increment: Decimal,
    taker_fee_rate: Decimal,
    funding_reserve_bps: Decimal,
) -> dict[str, Any]:
    if maximum_hold_seconds <= 0:
        raise ValueError("maximum_hold_seconds must be positive")
    state_logic.validate()
    mss_logic.validate()

    events, details_by_event, state_diagnostics = events_from_detector(
        seconds,
        detector_report=detector_report,
        logic=state_logic,
        require_value_normalization=require_value_normalization,
    )
    plans, mss_diagnostics = diagnose_mss_fvg(
        minutes,
        events=events,
        logic=mss_logic,
        require_fvg_retest=True,
    )

    work = (
        seconds.copy()
        .sort_values("timestamp_ns", kind="stable")
        .reset_index(drop=True)
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
    touch_cache: dict[str, int | None] = {}
    slot_end = -1
    target_map = {
        state_logic.target_timeframe: tuple(
            target_pools.get(state_logic.target_timeframe, ())
        )
    }

    for plan in sorted(
        plans,
        key=lambda item: (item.observed_ns, item.scenario_id),
    ):
        entry_index = _first_second_after_completed_signal(
            timestamps,
            plan.observed_ns,
        )
        if entry_index is None:
            counters["NO_POST_SIGNAL_TRADE_SECOND"] += 1
            continue
        if entry_index <= slot_end:
            counters["SIGNAL_DURING_ACTIVE_SLOT"] += 1
            continue
        row = work.iloc[entry_index]
        entry = float(row["close"])
        stop = float(plan.stop)
        risk = entry - stop if plan.direction == "LONG" else stop - entry
        stop_in_entry_second = (
            float(row["low"]) <= stop
            if plan.direction == "LONG"
            else float(row["high"]) >= stop
        )
        if risk <= 0.0 or stop_in_entry_second:
            counters["POST_SIGNAL_SOURCE_INVALIDATED"] += 1
            continue

        selected = target_pool_after_complete_confirmation_second(
            target_map,
            direction=plan.direction,
            entry=entry,
            stop=stop,
            entry_index=entry_index,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
            touch_cache=touch_cache,
            minimum_rr=0.0,
        )
        if selected is None:
            counters["NO_CAUSAL_UNCONSUMED_5M_TARGET"] += 1
            continue
        target_pool, gross_rr = selected
        geometry = adverse_execution_geometry(
            direction=plan.direction,
            entry_reference=Decimal(str(entry)),
            stop_price=Decimal(str(stop)),
            target_price=Decimal(str(target_pool.level)),
            price_increment=price_increment,
            taker_fee_rate=taker_fee_rate,
            funding_reserve_bps=funding_reserve_bps,
            adverse_slippage_ticks=1,
        )
        if not geometry.target_is_net_positive:
            counters["FIRST_EXTERNAL_TARGET_NOT_NET_POSITIVE"] += 1
            scenarios.append(
                {
                    "scenario_id": plan.scenario_id,
                    "outcome": "FIRST_EXTERNAL_TARGET_NOT_NET_POSITIVE",
                    "event": details_by_event[plan.event_id],
                    "entry": entry,
                    "stop": stop,
                    "target": float(target_pool.level),
                    "target_pool_id": target_pool.pool_id,
                    "gross_rr": float(gross_rr),
                    "cost_adjusted_target_r": float(
                        geometry.cost_adjusted_target_r
                    ),
                }
            )
            continue

        path, terminal_index = impact._path_result(
            work,
            start_index=entry_index,
            direction=plan.direction,
            entry=entry,
            stop=stop,
            target=float(target_pool.level),
            max_hold_seconds=maximum_hold_seconds,
        )
        slot_end = max(slot_end, terminal_index)
        realized_cost_r = _cost_adjusted_terminal_r(
            path=path,
            direction=plan.direction,
            entry=Decimal(str(entry)),
            stop=Decimal(str(stop)),
            geometry=geometry,
            price_increment=price_increment,
            taker_fee_rate=taker_fee_rate,
        )
        counters["ENTRY_READY"] += 1
        scenarios.append(
            {
                "scenario_id": plan.scenario_id,
                "outcome": "ENTRY_READY",
                "direction": plan.direction,
                "event": details_by_event[plan.event_id],
                "observed_ns": plan.observed_ns,
                "entry_second_ns": int(row["timestamp_ns"]),
                "entry": entry,
                "stop": stop,
                "risk": risk,
                "target": float(target_pool.level),
                "target_pool_id": target_pool.pool_id,
                "target_timeframe": target_pool.timeframe,
                "gross_rr": float(gross_rr),
                "cost_adjusted_target_r": float(
                    geometry.cost_adjusted_target_r
                ),
                "realized_cost_adjusted_r": float(realized_cost_r),
                "mss_ns": plan.mss_ns,
                "mss_swing_id": plan.mss_swing_id,
                "mss_level": plan.mss_level,
                "fvg_id": plan.fvg_id,
                "fvg_lower": plan.fvg_lower,
                "fvg_upper": plan.fvg_upper,
                "retest_ns": plan.retest_ns,
                "body_atr": plan.body_atr,
                "displacement_rank": plan.displacement_rank,
                "cost_geometry": {
                    "expected_entry_fill": str(
                        geometry.expected_entry_fill
                    ),
                    "expected_stop_fill": str(
                        geometry.expected_stop_fill
                    ),
                    "expected_target_fill": str(
                        geometry.expected_target_fill
                    ),
                    "per_unit_expected_loss": str(
                        geometry.per_unit_expected_loss
                    ),
                    "per_unit_expected_target_gain": str(
                        geometry.per_unit_expected_target_gain
                    ),
                },
                "path": path,
            }
        )

    entries = [
        item for item in scenarios if item.get("outcome") == "ENTRY_READY"
    ]
    outcomes = Counter(
        str(item["path"]["outcome"]) for item in entries
    )
    dates = Counter(
        pd.to_datetime(
            int(item["entry_second_ns"]),
            unit="ns",
            utc=True,
        )
        .date()
        .isoformat()
        for item in entries
    )
    realized = [
        float(item["realized_cost_adjusted_r"]) for item in entries
    ]
    winners = [value for value in realized if value > 0.0]
    gross_positive_r = sum(winners)
    winner_concentration = (
        max(winners) / gross_positive_r
        if winners and gross_positive_r > 0.0
        else None
    )
    mfe = [float(item["path"]["mfe_r"]) for item in entries]
    mae = [float(item["path"]["mae_r"]) for item in entries]
    maximum_day_share = (
        max(dates.values()) / len(entries) if entries else None
    )
    gross_cost_r = float(sum(realized))
    gate = {
        "minimum_entry_ready": len(entries) >= 7,
        "minimum_active_days": len(dates) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "positive_cost_adjusted_structural_r": gross_cost_r > 0.0,
        "median_mae_below_one_r": (
            bool(mae) and float(pd.Series(mae).median()) < 1.0
        ),
        "maximum_day_share_at_most_55pct": (
            maximum_day_share is not None and maximum_day_share <= 0.55
        ),
        "maximum_single_winner_share_at_most_55pct": (
            winner_concentration is not None
            and winner_concentration <= 0.55
        ),
    }
    gate["passed"] = all(gate.values())

    state_counts = Counter(
        str(item["outcome"]) for item in state_diagnostics
    )
    mss_counts = Counter(item.outcome for item in mss_diagnostics)
    return {
        "summary": {
            "detector_accepted_events": sum(
                item.get("outcome") == "EVENT_ACCEPTED"
                for item in detector_report.get("scenarios", ())
            ),
            "mss_start_events": len(events),
            "value_state_diagnostic_counts": dict(
                sorted(state_counts.items())
            ),
            "mss_fvg_plans": len(plans),
            "mss_fvg_diagnostic_counts": dict(
                sorted(mss_counts.items())
            ),
            "path_counts": dict(sorted(counters.items())),
            "entry_ready": len(entries),
            "active_days": len(dates),
            "entries_by_day": dict(sorted(dates.items())),
            "maximum_day_share": maximum_day_share,
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "gross_cost_adjusted_structural_r": gross_cost_r,
            "mean_cost_adjusted_structural_r": (
                gross_cost_r / len(entries) if entries else None
            ),
            "median_cost_adjusted_target_r": (
                float(
                    pd.Series(
                        [
                            item["cost_adjusted_target_r"]
                            for item in entries
                        ]
                    ).median()
                )
                if entries
                else None
            ),
            "median_mfe_r": (
                float(pd.Series(mfe).median()) if mfe else None
            ),
            "median_mae_r": (
                float(pd.Series(mae).median()) if mae else None
            ),
            "single_winner_share": winner_concentration,
            "diagnostic_gate": gate,
        },
        "state_diagnostics": state_diagnostics,
        "mss_fvg_diagnostics": [
            {
                "event_id": item.event_id,
                "outcome": item.outcome,
                "details": item.details,
            }
            for item in mss_diagnostics
        ],
        "scenarios": scenarios,
    }


__all__ = [
    "ValueNormalizedMSSFVGLogic",
    "evaluate",
    "events_from_detector",
    "find_value_normalization",
]
