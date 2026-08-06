#!/usr/bin/env python3
"""Causal micro-auction balance expansion, retest and failure states.

Prior candidates found useful boundary negotiation but insufficient frequency,
and frequent flow-only candidates confused aggressive effort with its market
result.  This candidate defines a complete repeated auction unit:

    completed micro balance
    -> first close outside its frozen boundary
    -> inventory route at the break
    -> either exact boundary retest and renewed expansion
       or completed re-entry and failed discovery

The balance is built only from bars completed before the break.  Its width must
be low relative to past auctions, its path must be rotational, its net movement
must be inefficient and OI must be comparatively stable.  Breakouts require
completed displacement, aggressor flow, notional, price-impact efficiency and
basis alignment.  OI separates newly created inventory from forced liquidation.

New inventory can continue only if a weak counter-flow retest holds the exact
boundary and retains the created OI.  A liquidation break can continue only if
the retest holds while OI remains depleted.  Re-entry into the frozen balance
routes either trapped new inventory or liquidation exhaustion.  Structural
stops lie beyond the complete break/retest or break/re-entry excursion.

The compiler emits completed-data intents only.  NautilusTrader remains the
sole owner of targets, orders, fills, fees, positions, margin, liquidation,
current-NAV 3% risk sizing, PnL and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401


Intent = v22.Intent

NEW_INVENTORY_CONTINUATION = "MICRO_BALANCE_NEW_INVENTORY_RETEST_CONTINUATION"
LIQUIDATION_CONTINUATION = "MICRO_BALANCE_LIQUIDATION_RETEST_CONTINUATION"
TRAPPED_INVENTORY_REVERSAL = "MICRO_BALANCE_TRAPPED_BREAKOUT_REVERSAL"
LIQUIDATION_EXHAUSTION_REVERSAL = "MICRO_BALANCE_LIQUIDATION_EXHAUSTION_REVERSAL"

BALANCE_BARS = 30
BALANCE_MAX_AGE_BARS = 45
OUTCOME_BARS = 8
RESUMPTION_BARS = 5
COOLDOWN_BARS = 12
QUANTILE_WINDOW = 720
QUANTILE_MINIMUM = 240
MIN_BREAK_ATR = 0.03
MAX_BREAK_ATR = 0.85
MIN_BODY_ATR = 0.20
MIN_CLOSE_LOCATION = 0.65
RETEST_TOUCH_ATR = 0.12
MAX_COUNTER_EFFORT_FRACTION = 0.60
NEW_INVENTORY_RETENTION = 0.999
LIQUIDATION_REBUILD_TOLERANCE = 1.001


@dataclass(frozen=True, slots=True)
class BalanceMetrics:
    high: pd.Series
    low: pd.Series
    width_atr: pd.Series
    path_to_width: pd.Series
    net_efficiency: pd.Series
    oi_dispersion: pd.Series
    close_location: pd.Series


@dataclass(frozen=True, slots=True)
class Thresholds:
    width_atr_q45: pd.Series
    path_to_width_q55: pd.Series
    net_efficiency_q45: pd.Series
    oi_dispersion_q60: pd.Series
    abs_flow_q65: pd.Series
    abs_return_q65: pd.Series
    notional_burst_q60: pd.Series
    impact_efficiency_q55: pd.Series
    positive_oi_step_median: pd.Series


@dataclass(frozen=True, slots=True)
class FrozenBalance:
    balance_id: int
    start_index: int
    end_index: int
    created_index: int
    expires_index: int
    high: float
    low: float
    midpoint: float
    width: float
    atr: float
    width_atr: float
    path_to_width: float
    net_efficiency: float
    oi_dispersion: float


@dataclass(frozen=True, slots=True)
class BreakState:
    balance: FrozenBalance
    index: int
    side: int
    boundary: float
    close: float
    atr: float
    penetration_atr: float
    effort: float
    oi_before: float
    oi_at_break: float
    oi_change: float
    inventory_route: str


def finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def shifted_quantile(
    series: pd.Series,
    quantile: float,
    window: int = QUANTILE_WINDOW,
    minimum: int = QUANTILE_MINIMUM,
) -> pd.Series:
    """Past-only rolling quantile; the current observation is always excluded."""

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
    return finite(data["metric_sum_open_interest"].iloc[index])


def build_balance_metrics(data: pd.DataFrame) -> BalanceMetrics:
    high = data["high"].astype(float).rolling(BALANCE_BARS, min_periods=BALANCE_BARS).max()
    low = data["low"].astype(float).rolling(BALANCE_BARS, min_periods=BALANCE_BARS).min()
    width = high - low
    atr = data["atr"].astype(float)
    width_atr = width / atr.replace(0.0, float("nan"))
    close = data["close"].astype(float)
    path = close.diff().abs().rolling(BALANCE_BARS, min_periods=BALANCE_BARS).sum()
    path_to_width = path / width.replace(0.0, float("nan"))
    net = (close - close.shift(BALANCE_BARS - 1)).abs()
    net_efficiency = net / path.replace(0.0, float("nan"))
    oi = data["metric_sum_open_interest"].astype(float)
    oi_mean = oi.rolling(BALANCE_BARS, min_periods=BALANCE_BARS).mean()
    oi_dispersion = (
        oi.rolling(BALANCE_BARS, min_periods=BALANCE_BARS).max()
        - oi.rolling(BALANCE_BARS, min_periods=BALANCE_BARS).min()
    ) / oi_mean.replace(0.0, float("nan"))
    close_location = (close - low) / width.replace(0.0, float("nan"))
    return BalanceMetrics(
        high=high,
        low=low,
        width_atr=width_atr,
        path_to_width=path_to_width,
        net_efficiency=net_efficiency,
        oi_dispersion=oi_dispersion,
        close_location=close_location,
    )


def build_thresholds(
    data: pd.DataFrame,
    metrics: BalanceMetrics,
    config: Any,
) -> Thresholds:
    window = int(getattr(config, "stress_inventory_quantile_window_minutes", QUANTILE_WINDOW))
    minimum = int(getattr(config, "stress_inventory_quantile_min_periods", QUANTILE_MINIMUM))
    oi = data["metric_sum_open_interest"].astype(float)
    positive_oi_step = oi.pct_change(fill_method=None).where(lambda values: values > 0.0)
    return Thresholds(
        width_atr_q45=shifted_quantile(metrics.width_atr, 0.45, window, minimum),
        path_to_width_q55=shifted_quantile(metrics.path_to_width, 0.55, window, minimum),
        net_efficiency_q45=shifted_quantile(metrics.net_efficiency, 0.45, window, minimum),
        oi_dispersion_q60=shifted_quantile(metrics.oi_dispersion, 0.60, window, minimum),
        abs_flow_q65=shifted_quantile(data["flow_60s"].abs(), 0.65, window, minimum),
        abs_return_q65=shifted_quantile(data["ret_60s_bps"].abs(), 0.65, window, minimum),
        notional_burst_q60=shifted_quantile(data["notional_burst_60s"], 0.60, window, minimum),
        impact_efficiency_q55=shifted_quantile(data["eff_60s"], 0.55, window, minimum),
        positive_oi_step_median=shifted_quantile(
            positive_oi_step,
            0.50,
            window,
            max(30, minimum // 4),
        ),
    )


def balance_qualifies(
    state_index: int,
    metrics: BalanceMetrics,
    thresholds: Thresholds,
) -> bool:
    values = (
        finite(metrics.width_atr.iloc[state_index]),
        finite(thresholds.width_atr_q45.iloc[state_index]),
        finite(metrics.path_to_width.iloc[state_index]),
        finite(thresholds.path_to_width_q55.iloc[state_index]),
        finite(metrics.net_efficiency.iloc[state_index]),
        finite(thresholds.net_efficiency_q45.iloc[state_index]),
        finite(metrics.oi_dispersion.iloc[state_index]),
        finite(thresholds.oi_dispersion_q60.iloc[state_index]),
        finite(metrics.close_location.iloc[state_index]),
    )
    if not all(math.isfinite(value) for value in values):
        return False
    return bool(
        0.0 < values[0] <= values[1]
        and values[2] >= values[3]
        and values[4] <= values[5]
        and values[6] <= values[7]
        and 0.20 <= values[8] <= 0.80
    )


def freeze_balance(
    balance_id: int,
    state_index: int,
    data: pd.DataFrame,
    metrics: BalanceMetrics,
) -> FrozenBalance | None:
    high = finite(metrics.high.iloc[state_index])
    low = finite(metrics.low.iloc[state_index])
    atr = finite(data["atr"].iloc[state_index])
    values = (
        high,
        low,
        atr,
        finite(metrics.width_atr.iloc[state_index]),
        finite(metrics.path_to_width.iloc[state_index]),
        finite(metrics.net_efficiency.iloc[state_index]),
        finite(metrics.oi_dispersion.iloc[state_index]),
    )
    if not all(math.isfinite(value) for value in values):
        return None
    if high <= low or atr <= 0.0:
        return None
    return FrozenBalance(
        balance_id=balance_id,
        start_index=state_index - BALANCE_BARS + 1,
        end_index=state_index,
        created_index=state_index + 1,
        expires_index=state_index + BALANCE_MAX_AGE_BARS,
        high=high,
        low=low,
        midpoint=0.5 * (high + low),
        width=high - low,
        atr=atr,
        width_atr=values[3],
        path_to_width=values[4],
        net_efficiency=values[5],
        oi_dispersion=values[6],
    )


def classify_inventory_route(
    oi_before: float,
    oi_at_break: float,
    material_cutoff: float,
) -> tuple[str, float] | None:
    values = (oi_before, oi_at_break, material_cutoff)
    if not all(math.isfinite(value) for value in values):
        return None
    if oi_before <= 0.0 or oi_at_break <= 0.0 or material_cutoff <= 0.0:
        return None
    change = oi_at_break / oi_before - 1.0
    if change >= material_cutoff:
        return "NEW_INVENTORY", change
    if change <= -material_cutoff:
        return "LIQUIDATION", change
    return None


def breakout_state(
    data: pd.DataFrame,
    index: int,
    balance: FrozenBalance,
    thresholds: Thresholds,
) -> BreakState | None:
    row = data.iloc[index]
    close = finite(row["close"])
    open_price = finite(row["open"])
    high = finite(row["high"])
    low = finite(row["low"])
    atr = finite(row["atr"])
    if not all(math.isfinite(value) for value in (close, open_price, high, low, atr)):
        return None
    if atr <= 0.0 or high <= low:
        return None
    side = 1 if close > balance.high else -1 if close < balance.low else 0
    if side == 0:
        return None
    boundary = balance.high if side > 0 else balance.low
    penetration = side * (close - boundary) / atr
    body = side * (close - open_price) / atr
    close_location = (close - low) / (high - low)
    directional_close_location = close_location if side > 0 else 1.0 - close_location
    flow = side * finite(row["flow_60s"])
    return_bps = side * finite(row["ret_60s_bps"])
    notional_burst = finite(row["notional_burst_60s"])
    impact_efficiency = finite(row["eff_60s"])
    basis = side * finite(row["basis_change_5m"])
    cutoffs = (
        finite(thresholds.abs_flow_q65.iloc[index]),
        finite(thresholds.abs_return_q65.iloc[index]),
        finite(thresholds.notional_burst_q60.iloc[index]),
        finite(thresholds.impact_efficiency_q55.iloc[index]),
    )
    if not all(
        math.isfinite(value)
        for value in (
            penetration,
            body,
            directional_close_location,
            flow,
            return_bps,
            notional_burst,
            impact_efficiency,
            basis,
            *cutoffs,
        )
    ):
        return None
    if not (
        MIN_BREAK_ATR <= penetration <= MAX_BREAK_ATR
        and body >= MIN_BODY_ATR
        and directional_close_location >= MIN_CLOSE_LOCATION
        and flow >= cutoffs[0]
        and return_bps >= cutoffs[1]
        and notional_burst >= cutoffs[2]
        and impact_efficiency >= cutoffs[3]
        and basis > 0.0
    ):
        return None
    oi_before = _oi(data, index - 15)
    oi_at_break = _oi(data, index)
    route = classify_inventory_route(
        oi_before,
        oi_at_break,
        finite(thresholds.positive_oi_step_median.iloc[index]),
    )
    if route is None:
        return None
    inventory_route, oi_change = route
    notional = max(finite(row["notional_60s"]), 0.0)
    effort = max(flow, 0.0) * notional
    return BreakState(
        balance=balance,
        index=index,
        side=side,
        boundary=boundary,
        close=close,
        atr=atr,
        penetration_atr=penetration,
        effort=effort,
        oi_before=oi_before,
        oi_at_break=oi_at_break,
        oi_change=oi_change,
        inventory_route=inventory_route,
    )


def boundary_reentered(close: float, break_state: BreakState) -> bool:
    if not math.isfinite(close):
        return False
    return close < break_state.boundary if break_state.side > 0 else close > break_state.boundary


def boundary_retest_holds(
    high: float,
    low: float,
    close: float,
    break_state: BreakState,
) -> bool:
    if not all(math.isfinite(value) for value in (high, low, close)):
        return False
    tolerance = RETEST_TOUCH_ATR * break_state.atr
    if break_state.side > 0:
        touched = low <= break_state.boundary + tolerance
        held = close >= break_state.boundary
    else:
        touched = high >= break_state.boundary - tolerance
        held = close <= break_state.boundary
    return touched and held


def _directional_effort(row: pd.Series, side: int) -> float:
    flow = side * finite(row["flow_60s"])
    notional = max(finite(row["notional_60s"]), 0.0)
    if not all(math.isfinite(value) for value in (flow, notional)):
        return float("nan")
    return max(flow, 0.0) * notional


def _stop_buffer(impact_parameters: Any) -> float:
    value = getattr(impact_parameters, "stop_buffer_atr", None)
    if value is None:
        value = getattr(impact_parameters, "sweep_stop_buffer_atr", None)
    if value is None:
        raise AttributeError("impact configuration has no structural stop buffer")
    return float(value)


def excursion_stop(
    data: pd.DataFrame,
    start: int,
    end: int,
    side: int,
    impact_parameters: Any,
) -> float:
    segment = data.iloc[start : end + 1]
    atr = finite(data["atr"].iloc[end])
    if segment.empty or not math.isfinite(atr) or atr <= 0.0:
        return float("nan")
    extreme = float(segment["low"].min() if side > 0 else segment["high"].max())
    return extreme - side * _stop_buffer(impact_parameters) * atr


def _copy_details(
    state: BreakState,
    outcome_index: int,
    outcome: str,
) -> dict[str, Any]:
    balance = state.balance
    return {
        "balance_id": balance.balance_id,
        "balance_start_index": balance.start_index,
        "balance_end_index": balance.end_index,
        "balance_high": balance.high,
        "balance_low": balance.low,
        "balance_midpoint": balance.midpoint,
        "balance_width_atr": balance.width_atr,
        "balance_path_to_width": balance.path_to_width,
        "balance_net_efficiency": balance.net_efficiency,
        "balance_oi_dispersion": balance.oi_dispersion,
        "break_index": state.index,
        "break_side": state.side,
        "break_boundary": state.boundary,
        "break_penetration_atr": state.penetration_atr,
        "break_effort": state.effort,
        "oi_before_break": state.oi_before,
        "oi_at_break": state.oi_at_break,
        "break_oi_change": state.oi_change,
        "break_inventory_route": state.inventory_route,
        "outcome_index": outcome_index,
        "auction_outcome": outcome,
        "compiler": "candidate-04-micro-auction-balance-transition",
    }


def resolve_break(
    data: pd.DataFrame,
    state: BreakState,
    evaluation_end: pd.Timestamp,
    thresholds: Thresholds,
    impact_parameters: Any,
) -> tuple[Intent | None, int, dict[str, int]]:
    counts = {
        "reentry_without_confirmation": 0,
        "new_inventory_not_unwound": 0,
        "liquidation_not_exhausted": 0,
        "retest_counter_effort_too_large": 0,
        "retest_inventory_not_retained": 0,
        "retest_no_resumption": 0,
    }
    upper = min(state.index + OUTCOME_BARS, len(data) - 2)
    retest_index: int | None = None
    for index in range(state.index + 1, upper + 1):
        if data.index[index] > evaluation_end:
            return None, index, counts
        row = data.iloc[index]
        close = finite(row["close"])
        flow_opposite = -state.side * finite(row["flow_60s"])
        return_opposite = -state.side * finite(row["ret_60s_bps"])
        basis_opposite = -state.side * finite(row["basis_change_5m"])
        oi_current = _oi(data, index)

        if boundary_reentered(close, state):
            confirmed = all(
                math.isfinite(value) and value > 0.0
                for value in (flow_opposite, return_opposite, basis_opposite)
            )
            if not confirmed:
                counts["reentry_without_confirmation"] += 1
                continue
            if state.inventory_route == "NEW_INVENTORY":
                if not (
                    math.isfinite(oi_current)
                    and oi_current < state.oi_at_break
                ):
                    counts["new_inventory_not_unwound"] += 1
                    continue
                scenario = TRAPPED_INVENTORY_REVERSAL
                outcome = "NEW_INVENTORY_FAILED_AND_UNWOUND"
            else:
                if not (
                    math.isfinite(oi_current)
                    and oi_current <= state.oi_at_break * LIQUIDATION_REBUILD_TOLERANCE
                ):
                    counts["liquidation_not_exhausted"] += 1
                    continue
                scenario = LIQUIDATION_EXHAUSTION_REVERSAL
                outcome = "LIQUIDATION_BREAK_FAILED_AND_REENTERED"
            trade_side = -state.side
            stop = excursion_stop(data, state.index, index, trade_side, impact_parameters)
            if not math.isfinite(stop) or trade_side * (close - stop) <= 0.0:
                continue
            details = {
                **_copy_details(state, index, outcome),
                "confirmation_directional_flow_60s": flow_opposite,
                "confirmation_directional_return_60s_bps": return_opposite,
                "confirmation_directional_basis_change_5m_bps": basis_opposite,
                "oi_at_confirmation": oi_current,
            }
            return (
                Intent(
                    scenario=scenario,
                    side=trade_side,
                    signal_index=index,
                    entry_index=index + 1,
                    stop_level=stop,
                    event_indices=(state.balance.start_index, state.balance.end_index, state.index, index),
                    details=details,
                ),
                index,
                counts,
            )

        if retest_index is None and boundary_retest_holds(
            finite(row["high"]),
            finite(row["low"]),
            close,
            state,
        ):
            segment = data.iloc[state.index + 1 : index + 1]
            counter_effort = sum(
                value
                for _, item in segment.iterrows()
                if math.isfinite(value := _directional_effort(item, -state.side))
            )
            if not (
                math.isfinite(state.effort)
                and state.effort > 0.0
                and counter_effort <= MAX_COUNTER_EFFORT_FRACTION * state.effort
            ):
                counts["retest_counter_effort_too_large"] += 1
                continue
            if state.inventory_route == "NEW_INVENTORY":
                retained = (
                    math.isfinite(oi_current)
                    and oi_current >= NEW_INVENTORY_RETENTION * state.oi_at_break
                )
            else:
                retained = (
                    math.isfinite(oi_current)
                    and oi_current <= LIQUIDATION_REBUILD_TOLERANCE * state.oi_at_break
                )
            if not retained:
                counts["retest_inventory_not_retained"] += 1
                continue
            retest_index = index
            break

    if retest_index is None:
        return None, upper, counts

    retest_segment = data.iloc[state.index + 1 : retest_index + 1]
    structure = float(
        retest_segment["high"].max()
        if state.side > 0
        else retest_segment["low"].min()
    )
    confirm_upper = min(retest_index + RESUMPTION_BARS, len(data) - 2)
    for index in range(retest_index + 1, confirm_upper + 1):
        if data.index[index] > evaluation_end:
            return None, index, counts
        row = data.iloc[index]
        close = finite(row["close"])
        structure_broken = close > structure if state.side > 0 else close < structure
        if not structure_broken:
            continue
        flow = state.side * finite(row["flow_60s"])
        return_bps = state.side * finite(row["ret_60s_bps"])
        basis = state.side * finite(row["basis_change_5m"])
        impact = finite(row["eff_60s"])
        cutoffs = (
            0.50 * finite(thresholds.abs_flow_q65.iloc[index]),
            finite(thresholds.impact_efficiency_q55.iloc[index]),
        )
        if not all(math.isfinite(value) for value in (flow, return_bps, basis, impact, *cutoffs)):
            continue
        if not (
            flow >= max(0.05, cutoffs[0])
            and return_bps > 0.0
            and basis > 0.0
            and impact >= cutoffs[1]
        ):
            continue
        oi_current = _oi(data, index)
        if state.inventory_route == "NEW_INVENTORY":
            inventory_ok = (
                math.isfinite(oi_current)
                and oi_current >= NEW_INVENTORY_RETENTION * state.oi_at_break
            )
            scenario = NEW_INVENTORY_CONTINUATION
            outcome = "NEW_INVENTORY_RETEST_HELD_AND_RESUMED"
        else:
            inventory_ok = (
                math.isfinite(oi_current)
                and oi_current <= LIQUIDATION_REBUILD_TOLERANCE * state.oi_at_break
            )
            scenario = LIQUIDATION_CONTINUATION
            outcome = "LIQUIDATION_RETEST_HELD_AND_RESUMED"
        if not inventory_ok:
            continue
        stop = excursion_stop(data, state.index + 1, index, state.side, impact_parameters)
        if not math.isfinite(stop) or state.side * (close - stop) <= 0.0:
            continue
        details = {
            **_copy_details(state, index, outcome),
            "retest_index": retest_index,
            "retest_structure": structure,
            "confirmation_directional_flow_60s": flow,
            "confirmation_directional_return_60s_bps": return_bps,
            "confirmation_directional_basis_change_5m_bps": basis,
            "confirmation_impact_efficiency_60s": impact,
            "oi_at_confirmation": oi_current,
        }
        return (
            Intent(
                scenario=scenario,
                side=state.side,
                signal_index=index,
                entry_index=index + 1,
                stop_level=stop,
                event_indices=(state.balance.start_index, state.balance.end_index, state.index, retest_index, index),
                details=details,
            ),
            index,
            counts,
        )
    counts["retest_no_resumption"] += 1
    return None, confirm_upper, counts


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    del router
    metrics = build_balance_metrics(data)
    thresholds = build_thresholds(data, metrics, config)
    intents: list[Intent] = []
    active: FrozenBalance | None = None
    balance_id = 0
    next_allowed = BALANCE_BARS
    counts = {
        "qualified_balances": 0,
        "expired_without_break": 0,
        "boundary_closes_not_qualified": 0,
        "new_inventory_breaks": 0,
        "liquidation_breaks": 0,
        "new_inventory_continuations": 0,
        "liquidation_continuations": 0,
        "trapped_inventory_reversals": 0,
        "liquidation_exhaustion_reversals": 0,
        "unresolved_breaks": 0,
        "resolution_diagnostics": {},
    }

    index = max(BALANCE_BARS, QUANTILE_MINIMUM)
    while index < len(data) - 1:
        timestamp = data.index[index]
        if timestamp > evaluation_end:
            break
        if active is None:
            if index < next_allowed:
                index += 1
                continue
            state_index = index - 1
            if not balance_qualifies(state_index, metrics, thresholds):
                index += 1
                continue
            balance_id += 1
            active = freeze_balance(balance_id, state_index, data, metrics)
            if active is None:
                index += 1
                continue
            counts["qualified_balances"] += 1

        if index > active.expires_index:
            counts["expired_without_break"] += 1
            active = None
            next_allowed = index + COOLDOWN_BARS
            index += 1
            continue

        state = breakout_state(data, index, active, thresholds)
        if state is None:
            close = finite(data["close"].iloc[index])
            if math.isfinite(close) and (close > active.high or close < active.low):
                counts["boundary_closes_not_qualified"] += 1
                active = None
                next_allowed = index + COOLDOWN_BARS
            index += 1
            continue

        if state.inventory_route == "NEW_INVENTORY":
            counts["new_inventory_breaks"] += 1
        else:
            counts["liquidation_breaks"] += 1
        intent, resolved_index, diagnostics = resolve_break(
            data,
            state,
            evaluation_end,
            thresholds,
            impact_parameters,
        )
        for key, value in diagnostics.items():
            bucket = counts["resolution_diagnostics"]
            bucket[key] = int(bucket.get(key, 0)) + int(value)
        if intent is None:
            counts["unresolved_breaks"] += 1
        else:
            intents.append(intent)
            if intent.scenario == NEW_INVENTORY_CONTINUATION:
                counts["new_inventory_continuations"] += 1
            elif intent.scenario == LIQUIDATION_CONTINUATION:
                counts["liquidation_continuations"] += 1
            elif intent.scenario == TRAPPED_INVENTORY_REVERSAL:
                counts["trapped_inventory_reversals"] += 1
            elif intent.scenario == LIQUIDATION_EXHAUSTION_REVERSAL:
                counts["liquidation_exhaustion_reversals"] += 1
        active = None
        next_allowed = max(index, resolved_index) + COOLDOWN_BARS
        index = max(index + 1, resolved_index + 1)

    intents.sort(key=lambda item: int(item.signal_index))
    unique: list[Intent] = []
    seen: set[int] = set()
    duplicates = 0
    for intent in intents:
        signal_index = int(intent.signal_index)
        if signal_index in seen:
            duplicates += 1
            continue
        seen.add(signal_index)
        unique.append(intent)
    return unique, {
        "candidate": "candidate-04-v36-micro-auction-balance-transition",
        "compiler": "candidate-04-micro-auction-balance-transition",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(unique),
        "duplicate_signal_bars": duplicates,
        "route_counts": counts,
        "scenario_contract": {
            "balance": (
                "frozen completed 30-minute low-width rotational low-efficiency "
                "auction with comparatively stable OI"
            ),
            "break": (
                "first completed boundary close with displacement, flow, notional, "
                "impact efficiency, basis and material OI route"
            ),
            "continuation": (
                "exact weak boundary retest plus route-consistent OI state and "
                "renewed efficient directional expansion"
            ),
            "reversal": (
                "completed re-entry into the exact frozen balance with opposite "
                "flow return basis and route-consistent inventory resolution"
            ),
            "thresholds": "shifted and past-only",
            "execution": "NautilusTrader BacktestNode only",
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
