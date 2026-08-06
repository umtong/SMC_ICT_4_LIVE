#!/usr/bin/env python3
"""Event-time order-flow runs with causal impact and inventory resolution.

Clock-time bars hide whether a five-minute state is one persistent meta-order or
several unrelated bursts.  This compiler segments completed one-minute flow into
causal same-sign runs above the shifted past median.  It then distinguishes:

* information-bearing flow: large cumulative effort, efficient directional
  price impact, material OI creation and aligned futures-index basis;
* absorbed liquidity attack: large effort with low impact at the first take of
  a confirmed external pool while OI contracts.

Information-bearing runs may continue only after a bounded weak counter-flow
pullback retains the created inventory and a fresh event-time flow run breaks
that pullback.  Absorbed runs reverse only after the exact external pool is
reclaimed with opposite flow, return and basis.

The compiler emits completed-data intents and structural stops only.
NautilusTrader remains the sole owner of targets, orders, fills, costs,
positions, margin, liquidation, risk sizing, PnL and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v24 as v24


Intent = v22.Intent
INFORMED_CONTINUATION = "EVENT_TIME_INFORMED_FLOW_PULLBACK_CONTINUATION"
ABSORPTION_REVERSAL = "EVENT_TIME_EXTERNAL_LIQUIDITY_ABSORPTION_REVERSAL"

WINDOW = 720
MINIMUM = 240
MIN_RUN_BARS = 2
MAX_RUN_BARS = 12
PULLBACK_BARS = 12
CONFIRMATION_BARS = 8
REVERSAL_BARS = 5
COOLDOWN_BARS = 20
MIN_RETRACE = 0.15
MAX_RETRACE = 0.55
MAX_COUNTER_EFFORT = 0.50
MIN_OI_RETENTION = 0.999


@dataclass(frozen=True, slots=True)
class FlowRun:
    start_index: int
    end_index: int
    side: int
    bars: int
    cumulative_effort: float
    directional_return_bps: float
    path_bps: float
    efficiency: float
    high: float
    low: float
    start_open_interest: float
    end_open_interest: float
    open_interest_change: float


@dataclass(frozen=True, slots=True)
class RunThresholds:
    median_abs_flow: pd.Series
    tail_cumulative_effort: pd.Series
    directional_return_q60: pd.Series
    efficiency_q30: pd.Series
    efficiency_q70: pd.Series
    positive_oi_step_median: pd.Series


def number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def shifted_quantile(
    series: pd.Series,
    quantile: float,
    window: int = WINDOW,
    minimum: int = MINIMUM,
) -> pd.Series:
    return (
        series.astype(float)
        .replace([math.inf, -math.inf], float("nan"))
        .shift(1)
        .rolling(window, min_periods=minimum)
        .quantile(quantile)
    )


def _oi(data: pd.DataFrame, index: int) -> float:
    if not 0 <= index < len(data):
        return float("nan")
    return number(data["metric_sum_open_interest"].iloc[index])


def build_run_thresholds(data: pd.DataFrame, config: Any) -> RunThresholds:
    window = int(getattr(config, "stress_inventory_quantile_window_minutes", WINDOW))
    minimum = int(getattr(config, "stress_inventory_quantile_min_periods", MINIMUM))
    flow = data["flow_60s"].astype(float)
    notional = data["notional_60s"].astype(float).clip(lower=0.0)
    effort_5m = (flow.abs() * notional).rolling(5, min_periods=2).sum()
    signed_return = data["ret_60s_bps"].astype(float)
    path = signed_return.abs().rolling(MAX_RUN_BARS, min_periods=MIN_RUN_BARS).sum()
    directional = signed_return.abs().rolling(MIN_RUN_BARS, min_periods=MIN_RUN_BARS).sum()
    efficiency_proxy = directional / path.replace(0.0, float("nan"))
    oi = data["metric_sum_open_interest"].astype(float)
    positive_step = oi.pct_change(fill_method=None).where(lambda values: values > 0.0)
    return RunThresholds(
        median_abs_flow=shifted_quantile(flow.abs(), 0.50, window, minimum),
        tail_cumulative_effort=shifted_quantile(effort_5m, 0.80, window, minimum),
        directional_return_q60=shifted_quantile(
            signed_return.abs(), 0.60, window, minimum
        ),
        efficiency_q30=shifted_quantile(
            efficiency_proxy, 0.30, window, minimum
        ),
        efficiency_q70=shifted_quantile(
            efficiency_proxy, 0.70, window, minimum
        ),
        positive_oi_step_median=shifted_quantile(
            positive_step,
            0.50,
            window,
            max(30, minimum // 4),
        ),
    )


def _close_run(
    data: pd.DataFrame,
    start: int,
    end: int,
    side: int,
) -> FlowRun | None:
    bars = end - start + 1
    if bars < MIN_RUN_BARS or side not in (-1, 1):
        return None
    segment = data.iloc[start : end + 1]
    effort = 0.0
    for _, row in segment.iterrows():
        flow = side * number(row["flow_60s"])
        notional = max(number(row["notional_60s"]), 0.0)
        if math.isfinite(flow) and math.isfinite(notional):
            effort += max(flow, 0.0) * notional
    start_price = number(segment["open"].iloc[0])
    end_price = number(segment["close"].iloc[-1])
    if not all(math.isfinite(value) and value > 0.0 for value in (start_price, end_price)):
        return None
    directional_return = side * (end_price / start_price - 1.0) * 10_000.0
    path = float(segment["ret_60s_bps"].astype(float).abs().sum())
    efficiency = max(directional_return, 0.0) / path if path > 0.0 else 0.0
    oi_start = _oi(data, max(start - 1, 0))
    oi_end = _oi(data, end)
    oi_change = (
        oi_end / oi_start - 1.0
        if math.isfinite(oi_start)
        and oi_start > 0.0
        and math.isfinite(oi_end)
        and oi_end > 0.0
        else float("nan")
    )
    return FlowRun(
        start_index=start,
        end_index=end,
        side=side,
        bars=bars,
        cumulative_effort=effort,
        directional_return_bps=directional_return,
        path_bps=path,
        efficiency=efficiency,
        high=float(segment["high"].max()),
        low=float(segment["low"].min()),
        start_open_interest=oi_start,
        end_open_interest=oi_end,
        open_interest_change=oi_change,
    )


def build_flow_runs(
    data: pd.DataFrame,
    thresholds: RunThresholds,
) -> list[FlowRun]:
    """Segment completed bars into non-overlapping causal same-sign flow runs."""

    runs: list[FlowRun] = []
    start: int | None = None
    side = 0
    for index in range(len(data)):
        flow = number(data["flow_60s"].iloc[index])
        cutoff = number(thresholds.median_abs_flow.iloc[index])
        current_side = 1 if flow > 0.0 else -1 if flow < 0.0 else 0
        qualifies = (
            current_side != 0
            and math.isfinite(cutoff)
            and cutoff > 0.0
            and abs(flow) >= cutoff
        )
        continuation = (
            qualifies
            and start is not None
            and current_side == side
            and index - start < MAX_RUN_BARS
        )
        if continuation:
            continue
        if start is not None:
            closed = _close_run(data, start, index - 1, side)
            if closed is not None:
                runs.append(closed)
        if qualifies:
            start = index
            side = current_side
        else:
            start = None
            side = 0
    if start is not None:
        closed = _close_run(data, start, len(data) - 1, side)
        if closed is not None:
            runs.append(closed)
    return runs


def run_is_informed(
    run: FlowRun,
    thresholds: RunThresholds,
    data: pd.DataFrame,
) -> bool:
    index = run.end_index
    values = (
        run.cumulative_effort,
        number(thresholds.tail_cumulative_effort.iloc[index]),
        run.directional_return_bps,
        number(thresholds.directional_return_q60.iloc[index]),
        run.efficiency,
        number(thresholds.efficiency_q70.iloc[index]),
        run.open_interest_change,
        number(thresholds.positive_oi_step_median.iloc[index]),
        run.side * number(data["basis_change_5m"].iloc[index]),
    )
    if not all(math.isfinite(value) for value in values):
        return False
    return (
        values[0] >= values[1]
        and values[2] >= values[3]
        and values[4] >= values[5]
        and values[6] >= values[7] > 0.0
        and values[8] > 0.0
    )


def run_is_low_impact_attack(
    run: FlowRun,
    thresholds: RunThresholds,
) -> bool:
    index = run.end_index
    effort_cutoff = number(thresholds.tail_cumulative_effort.iloc[index])
    low_efficiency = number(thresholds.efficiency_q30.iloc[index])
    return bool(
        math.isfinite(effort_cutoff)
        and math.isfinite(low_efficiency)
        and math.isfinite(run.open_interest_change)
        and run.cumulative_effort >= effort_cutoff
        and run.efficiency <= low_efficiency
        and run.open_interest_change <= 0.0
    )


def pool_reclaimed(pool_side: int, level: float, close: float) -> bool:
    if pool_side not in (-1, 1):
        return False
    if not all(math.isfinite(value) for value in (level, close)):
        return False
    return close < level if pool_side > 0 else close > level


def _stop(
    data: pd.DataFrame,
    start: int,
    end: int,
    side: int,
    impact_parameters: Any,
) -> float:
    segment = data.iloc[start : end + 1]
    atr = number(data["atr"].iloc[end])
    if segment.empty or not math.isfinite(atr) or atr <= 0.0:
        return float("nan")
    extreme = float(segment["low"].min() if side > 0 else segment["high"].max())
    return extreme - side * float(impact_parameters.stop_buffer_atr) * atr


def _matching_pool_take(
    run: FlowRun,
    pool_takes: dict[int, list[Any]],
) -> Any | None:
    choices = []
    for index in range(run.start_index, run.end_index + 1):
        for take in pool_takes.get(index, []):
            if int(take.pool_side) == run.side:
                choices.append(take)
    if not choices:
        return None
    return max(
        choices,
        key=lambda take: (
            float(take.prominence_atr),
            int(take.age_bars),
            float(take.penetration_atr),
        ),
    )


def detect_absorption_reversals(
    data: pd.DataFrame,
    runs: list[FlowRun],
    thresholds: RunThresholds,
    pool_takes: dict[int, list[Any]],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    intents: list[Intent] = []
    counts = {
        "large_low_impact_runs": 0,
        "without_external_pool": 0,
        "no_exact_reclaim": 0,
        "confirmed_absorption_reversals": 0,
    }
    last_signal = -10**12
    for run in runs:
        if data.index[run.end_index] < evaluation_start:
            continue
        if data.index[run.end_index] > evaluation_end:
            break
        if run.end_index - last_signal < COOLDOWN_BARS:
            continue
        if not run_is_low_impact_attack(run, thresholds):
            continue
        counts["large_low_impact_runs"] += 1
        take = _matching_pool_take(run, pool_takes)
        if take is None:
            counts["without_external_pool"] += 1
            continue
        trade_side = -run.side
        upper = min(run.end_index + REVERSAL_BARS, len(data) - 2)
        confirmed = False
        for index in range(run.end_index + 1, upper + 1):
            if data.index[index] > evaluation_end:
                break
            row = data.iloc[index]
            close = number(row["close"])
            if not pool_reclaimed(int(take.pool_side), float(take.level), close):
                continue
            flow = trade_side * number(row["flow_60s"])
            ret = trade_side * number(row["ret_60s_bps"])
            basis = trade_side * number(row["basis_change_5m"])
            cutoff = number(thresholds.median_abs_flow.iloc[index])
            if not all(math.isfinite(value) for value in (flow, ret, basis, cutoff)):
                continue
            if not (flow >= cutoff and ret > 0.0 and basis > 0.0):
                continue
            stop = _stop(data, run.start_index, index, trade_side, impact_parameters)
            if not math.isfinite(stop) or trade_side * (close - stop) <= 0.0:
                continue
            details = {
                "flow_run": run.__dict__ if hasattr(run, "__dict__") else {
                    field: getattr(run, field)
                    for field in run.__dataclass_fields__
                },
                "pool_id": int(take.pool_id),
                "pool_side": int(take.pool_side),
                "pool_level": float(take.level),
                "pool_age_bars": int(take.age_bars),
                "pool_prominence_atr": float(take.prominence_atr),
                "confirmation_index": index,
                "confirmation_directional_flow_60s": flow,
                "confirmation_directional_return_60s_bps": ret,
                "confirmation_directional_basis_change_5m_bps": basis,
                "compiler": "candidate-04-event-time-flow-run",
            }
            intents.append(
                Intent(
                    scenario=ABSORPTION_REVERSAL,
                    side=trade_side,
                    signal_index=index,
                    entry_index=index + 1,
                    stop_level=stop,
                    event_indices=(run.start_index, run.end_index, index),
                    details=details,
                )
            )
            last_signal = index
            counts["confirmed_absorption_reversals"] += 1
            confirmed = True
            break
        if not confirmed:
            counts["no_exact_reclaim"] += 1
    return intents, counts


def detect_informed_continuations(
    data: pd.DataFrame,
    runs: list[FlowRun],
    thresholds: RunThresholds,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    intents: list[Intent] = []
    counts = {
        "informed_runs": 0,
        "no_bounded_pullback": 0,
        "counter_effort_too_large": 0,
        "inventory_not_retained": 0,
        "no_new_event_time_break": 0,
        "confirmed_continuations": 0,
    }
    last_signal = -10**12
    for run in runs:
        if data.index[run.end_index] < evaluation_start:
            continue
        if data.index[run.end_index] > evaluation_end:
            break
        if run.end_index - last_signal < COOLDOWN_BARS:
            continue
        if not run_is_informed(run, thresholds, data):
            continue
        counts["informed_runs"] += 1
        side = run.side
        start_price = number(data["open"].iloc[run.start_index])
        end_price = number(data["close"].iloc[run.end_index])
        displacement = side * (end_price - start_price)
        if not math.isfinite(displacement) or displacement <= 0.0:
            continue
        pullback: int | None = None
        retracement_value = float("nan")
        upper = min(run.end_index + PULLBACK_BARS, len(data) - 2)
        for index in range(run.end_index + 1, upper + 1):
            close = number(data["close"].iloc[index])
            retracement = side * (end_price - close) / displacement
            if retracement > MAX_RETRACE:
                break
            if retracement < MIN_RETRACE:
                continue
            segment = data.iloc[run.end_index + 1 : index + 1]
            counter_effort = 0.0
            for _, row in segment.iterrows():
                flow = -side * number(row["flow_60s"])
                notional = max(number(row["notional_60s"]), 0.0)
                if math.isfinite(flow) and math.isfinite(notional):
                    counter_effort += max(flow, 0.0) * notional
            if counter_effort > MAX_COUNTER_EFFORT * run.cumulative_effort:
                counts["counter_effort_too_large"] += 1
                break
            current_oi = _oi(data, index)
            if not (
                math.isfinite(current_oi)
                and math.isfinite(run.end_open_interest)
                and current_oi >= MIN_OI_RETENTION * run.end_open_interest
            ):
                counts["inventory_not_retained"] += 1
                break
            row = data.iloc[index]
            if not (
                -side * number(row["flow_60s"]) > 0.0
                and -side * number(row["ret_60s_bps"]) > 0.0
            ):
                continue
            pullback = index
            retracement_value = retracement
            break
        if pullback is None:
            counts["no_bounded_pullback"] += 1
            continue

        segment = data.iloc[run.end_index + 1 : pullback + 1]
        structure = float(
            segment["high"].max() if side > 0 else segment["low"].min()
        )
        confirm_upper = min(pullback + CONFIRMATION_BARS, len(data) - 2)
        confirmed = False
        for index in range(pullback + 1, confirm_upper + 1):
            if data.index[index] > evaluation_end:
                break
            row = data.iloc[index]
            close = number(row["close"])
            broken = close > structure if side > 0 else close < structure
            if not broken:
                continue
            flow = side * number(row["flow_60s"])
            ret = side * number(row["ret_60s_bps"])
            basis = side * number(row["basis_change_5m"])
            cutoff = number(thresholds.median_abs_flow.iloc[index])
            if not all(math.isfinite(value) for value in (flow, ret, basis, cutoff)):
                continue
            if not (
                flow >= cutoff
                and ret > 0.0
                and basis > 0.0
                and _oi(data, index) >= _oi(data, pullback)
            ):
                continue
            stop = _stop(data, run.end_index + 1, index, side, impact_parameters)
            if not math.isfinite(stop) or side * (close - stop) <= 0.0:
                continue
            details = {
                "flow_run": {
                    field: getattr(run, field)
                    for field in run.__dataclass_fields__
                },
                "pullback_index": pullback,
                "pullback_retracement_fraction": retracement_value,
                "pullback_structure": structure,
                "confirmation_index": index,
                "confirmation_directional_flow_60s": flow,
                "confirmation_directional_return_60s_bps": ret,
                "confirmation_directional_basis_change_5m_bps": basis,
                "compiler": "candidate-04-event-time-flow-run",
            }
            intents.append(
                Intent(
                    scenario=INFORMED_CONTINUATION,
                    side=side,
                    signal_index=index,
                    entry_index=index + 1,
                    stop_level=stop,
                    event_indices=(run.start_index, run.end_index, pullback, index),
                    details=details,
                )
            )
            counts["confirmed_continuations"] += 1
            last_signal = index
            confirmed = True
            break
        if not confirmed:
            counts["no_new_event_time_break"] += 1
    return intents, counts


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    del router
    thresholds = build_run_thresholds(data, config)
    runs = build_flow_runs(data, thresholds)
    pool_takes = v24.detect_external_pool_takes(data, config)
    reversals, reversal_counts = detect_absorption_reversals(
        data,
        runs,
        thresholds,
        pool_takes,
        evaluation_start,
        evaluation_end,
        impact_parameters,
    )
    continuations, continuation_counts = detect_informed_continuations(
        data,
        runs,
        thresholds,
        evaluation_start,
        evaluation_end,
        impact_parameters,
    )
    priority = {ABSORPTION_REVERSAL: 0, INFORMED_CONTINUATION: 1}
    combined = sorted(
        [*reversals, *continuations],
        key=lambda intent: (int(intent.signal_index), priority[str(intent.scenario)]),
    )
    unique: list[Intent] = []
    seen: set[int] = set()
    duplicates = 0
    for intent in combined:
        index = int(intent.signal_index)
        if index in seen:
            duplicates += 1
            continue
        seen.add(index)
        unique.append(intent)
    return unique, {
        "candidate": "candidate-04-v35-event-time-flow-runs",
        "compiler": "candidate-04-event-time-flow-run",
        "raw_flow_runs": len(runs),
        "raw_routed_signals": len(combined),
        "unique_signal_bars": len(unique),
        "duplicate_signal_bars": duplicates,
        "route_counts": {
            "absorption_reversal": reversal_counts,
            "informed_continuation": continuation_counts,
        },
        "scenario_contract": {
            "event_clock": "causal same-sign above-past-median one-minute flow runs",
            "information": "large effort, efficient impact, material OI creation and basis alignment",
            "absorption": "large low-impact external-pool attack with OI contraction",
            "continuation": "bounded weak pullback retains OI then a new event-time flow break",
            "reversal": "exact external-pool reclaim with opposite flow return and basis",
            "execution": "NautilusTrader BacktestNode only",
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
