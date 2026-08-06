#!/usr/bin/env python3
"""Causal post-event inventory resolution for frequent SMC/ICT day trades.

V32 classified absorption and continuation largely from the event or terminal
bar itself.  That was economically inconsistent: displayed depth may have been
resting before the attack, and a continuation bar should not require the low
impact, high absorption and decelerating flow which describe exhaustion.

This candidate separates three completed-data mechanisms:

1. liquidation absorption reversal
   A causal external pool is attacked by tail aggressor flow.  Aggressors keep
   pressing during the following completed bars, yet price reclaims the exact
   pool while OI is contracting.  Reversal flow, return and basis must align.

2. trapped breakout inventory reversal
   The same failed external attack initially creates material OI, but the new
   inventory contracts before confirmation.  This is distinct from liquidation
   at the attack itself.

3. informed inventory pullback continuation
   Persistent, efficient five-minute flow creates material OI.  A bounded
   counter-flow pullback retains that inventory.  Entry waits for an efficient,
   accelerating break of the completed pullback range.

All distributional boundaries are shifted and past-only.  The compiler emits
only scenario, side, observation time and structural invalidation.  Targets,
orders, fills, costs, positions, margin, liquidation, risk sizing and NAV remain
owned by NautilusTrader.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401  # warmup-aware loader
import rich_signal_compiler_v24 as v24


Intent = v22.Intent

LIQUIDATION_REVERSAL = "POST_ATTACK_LIQUIDATION_ABSORPTION_REVERSAL"
TRAPPED_INVENTORY_REVERSAL = "POST_ATTACK_TRAPPED_INVENTORY_REVERSAL"
INFORMED_CONTINUATION = "INFORMED_INVENTORY_PULLBACK_CONTINUATION"

WINDOW_BARS = 720
MIN_PERIODS = 240
ATTACK_RESPONSE_BARS = 6
EVENT_LOOKBACK_BARS = 5
PULLBACK_MAX_BARS = 20
RESUMPTION_MAX_BARS = 10
SIGNAL_COOLDOWN_BARS = 20
PULLBACK_MIN_RETRACE = 0.15
PULLBACK_MAX_RETRACE = 0.60
MAX_COUNTER_EFFORT_FRACTION = 0.60
MIN_INVENTORY_RETENTION = 0.999


@dataclass(frozen=True, slots=True)
class Thresholds:
    abs_flow_60_q70: pd.Series
    abs_return_60_q60: pd.Series
    notional_burst_60_q65: pd.Series
    abs_flow_300_q75: pd.Series
    abs_return_300_q65: pd.Series
    persistence_300_q60: pd.Series
    efficiency_300_q60: pd.Series
    efficiency_60_q50: pd.Series
    positive_oi_step_median: pd.Series


def finite(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def shifted_quantile(
    series: pd.Series,
    quantile: float,
    window: int = WINDOW_BARS,
    minimum: int = MIN_PERIODS,
) -> pd.Series:
    """Return a threshold whose value at t uses observations strictly before t."""

    return (
        series.astype(float)
        .replace([math.inf, -math.inf], float("nan"))
        .shift(1)
        .rolling(window, min_periods=minimum)
        .quantile(quantile)
    )


def build_thresholds(data: pd.DataFrame, config: Any) -> Thresholds:
    window = int(
        getattr(config, "stress_inventory_quantile_window_minutes", WINDOW_BARS)
    )
    minimum = int(
        getattr(config, "stress_inventory_quantile_min_periods", MIN_PERIODS)
    )
    oi = data["metric_sum_open_interest"].astype(float)
    positive_step = oi.pct_change(fill_method=None).where(lambda values: values > 0.0)
    return Thresholds(
        abs_flow_60_q70=shifted_quantile(data["flow_60s"].abs(), 0.70, window, minimum),
        abs_return_60_q60=shifted_quantile(
            data["ret_60s_bps"].abs(), 0.60, window, minimum
        ),
        notional_burst_60_q65=shifted_quantile(
            data["notional_burst_60s"], 0.65, window, minimum
        ),
        abs_flow_300_q75=shifted_quantile(
            data["flow_300s"].abs(), 0.75, window, minimum
        ),
        abs_return_300_q65=shifted_quantile(
            data["ret_300s_bps"].abs(), 0.65, window, minimum
        ),
        persistence_300_q60=shifted_quantile(
            data["flow_sign_persistence_300s"], 0.60, window, minimum
        ),
        efficiency_300_q60=shifted_quantile(
            data["eff_300s"], 0.60, window, minimum
        ),
        efficiency_60_q50=shifted_quantile(
            data["eff_60s"], 0.50, window, minimum
        ),
        positive_oi_step_median=shifted_quantile(
            positive_step,
            0.50,
            window,
            max(30, minimum // 4),
        ),
    )


def _oi(data: pd.DataFrame, index: int) -> float:
    if not 0 <= index < len(data):
        return float("nan")
    return finite(data["metric_sum_open_interest"].iloc[index])


def _directional_effort(row: pd.Series, side: int, seconds: int = 60) -> float:
    flow = finite(row[f"flow_{seconds}s"])
    notional = finite(row[f"notional_{seconds}s"])
    if side not in (-1, 1) or not all(math.isfinite(v) for v in (flow, notional)):
        return float("nan")
    return side * flow * max(notional, 0.0)


def classify_attack_inventory(
    pre_attack_oi: float,
    attack_oi: float,
    confirmation_oi: float,
    positive_step_cutoff: float,
) -> str | None:
    """Route a failed attack by when inventory was created and resolved."""

    values = (pre_attack_oi, attack_oi, confirmation_oi, positive_step_cutoff)
    if not all(math.isfinite(value) for value in values):
        return None
    if pre_attack_oi <= 0.0 or attack_oi <= 0.0 or confirmation_oi <= 0.0:
        return None
    attack_change = attack_oi / pre_attack_oi - 1.0
    if attack_change <= 0.0:
        return LIQUIDATION_REVERSAL
    if positive_step_cutoff > 0.0 and attack_change >= positive_step_cutoff:
        if confirmation_oi < attack_oi:
            return TRAPPED_INVENTORY_REVERSAL
    return None


def _response_stop(
    data: pd.DataFrame,
    start: int,
    end: int,
    trade_side: int,
    impact_parameters: Any,
) -> float:
    segment = data.iloc[start : end + 1]
    atr = finite(data["atr"].iloc[end])
    if segment.empty or not math.isfinite(atr) or atr <= 0.0:
        return float("nan")
    extreme = finite(
        segment["low"].min() if trade_side > 0 else segment["high"].max()
    )
    return extreme - trade_side * float(impact_parameters.stop_buffer_atr) * atr


def detect_post_attack_reversals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    thresholds: Thresholds,
) -> tuple[list[Intent], dict[str, int]]:
    takes = v24.detect_external_pool_takes(data, config)
    intents: list[Intent] = []
    counts = {
        "eligible_pool_takes": sum(len(items) for items in takes.values()),
        "attack_not_tail": 0,
        "no_persistent_attack_effort": 0,
        "no_exact_reclaim": 0,
        "ambiguous_inventory_route": 0,
        "liquidation_reversals": 0,
        "trapped_inventory_reversals": 0,
    }
    last_signal = -10**12

    for attack_index, candidates in sorted(takes.items()):
        if data.index[attack_index] < evaluation_start:
            continue
        if data.index[attack_index] > evaluation_end:
            break
        if attack_index - last_signal < SIGNAL_COOLDOWN_BARS:
            continue

        attack_row = data.iloc[attack_index]
        ranked: list[tuple[Any, float]] = []
        for take in candidates:
            attack_side = int(take.pool_side)
            flow = attack_side * finite(attack_row["flow_60s"])
            return_bps = attack_side * finite(attack_row["ret_60s_bps"])
            notional_burst = finite(attack_row["notional_burst_60s"])
            cutoffs = (
                finite(thresholds.abs_flow_60_q70.iloc[attack_index]),
                finite(thresholds.abs_return_60_q60.iloc[attack_index]),
                finite(thresholds.notional_burst_60_q65.iloc[attack_index]),
            )
            if not all(
                math.isfinite(value)
                for value in (flow, return_bps, notional_burst, *cutoffs)
            ):
                continue
            if not (
                flow >= cutoffs[0]
                and return_bps >= cutoffs[1]
                and notional_burst >= cutoffs[2]
            ):
                counts["attack_not_tail"] += 1
                continue
            ranked.append((take, flow * notional_burst))

        if not ranked:
            continue
        take = max(
            ranked,
            key=lambda item: (
                item[1],
                float(item[0].prominence_atr),
                int(item[0].age_bars),
            ),
        )[0]
        attack_side = int(take.pool_side)
        trade_side = int(take.trade_side)
        attack_close = finite(attack_row["close"])
        attack_midpoint = 0.5 * (
            finite(attack_row["high"]) + finite(attack_row["low"])
        )
        upper = min(attack_index + ATTACK_RESPONSE_BARS, len(data) - 2)
        had_effort = False
        confirmed = False

        for index in range(attack_index + 1, upper + 1):
            if data.index[index] > evaluation_end:
                break
            segment = data.iloc[attack_index : index + 1]
            cumulative_attack_effort = sum(
                max(_directional_effort(row, attack_side), 0.0)
                for _, row in segment.iterrows()
                if math.isfinite(_directional_effort(row, attack_side))
            )
            attack_event_effort = max(
                _directional_effort(attack_row, attack_side),
                0.0,
            )
            if not (
                math.isfinite(cumulative_attack_effort)
                and attack_event_effort > 0.0
                and cumulative_attack_effort >= 1.15 * attack_event_effort
            ):
                continue
            had_effort = True

            row = data.iloc[index]
            close = finite(row["close"])
            reclaimed = (
                close < float(take.level)
                if attack_side > 0
                else close > float(take.level)
            )
            midpoint_broken = trade_side * (close - attack_midpoint) > 0.0
            stalled_extension = attack_side * (close - attack_close) <= 0.0
            if not (reclaimed and midpoint_broken and stalled_extension):
                continue

            reversal_flow = trade_side * finite(row["flow_60s"])
            reversal_return = trade_side * finite(row["ret_60s_bps"])
            reversal_basis = trade_side * finite(row["basis_change_5m"])
            if not all(
                math.isfinite(value) and value > 0.0
                for value in (reversal_flow, reversal_return, reversal_basis)
            ):
                continue

            route = classify_attack_inventory(
                _oi(data, attack_index - 15),
                _oi(data, attack_index),
                _oi(data, index),
                finite(thresholds.positive_oi_step_median.iloc[attack_index]),
            )
            if route is None:
                counts["ambiguous_inventory_route"] += 1
                continue

            stop = _response_stop(
                data,
                attack_index,
                index,
                trade_side,
                impact_parameters,
            )
            if not math.isfinite(stop) or trade_side * (close - stop) <= 0.0:
                continue
            details = {
                "liquidity_source": "CAUSAL_CONFIRMED_EXTERNAL_PIVOT_POOL",
                "pool_id": int(take.pool_id),
                "pool_side": attack_side,
                "pool_level": float(take.level),
                "pool_age_bars": int(take.age_bars),
                "pool_prominence_atr": float(take.prominence_atr),
                "pool_penetration_atr": float(take.penetration_atr),
                "attack_index": attack_index,
                "confirmation_index": index,
                "response_bars": index - attack_index,
                "persistent_attack_effort": cumulative_attack_effort,
                "attack_event_effort": attack_event_effort,
                "reversal_directional_flow_60s": reversal_flow,
                "reversal_directional_return_60s_bps": reversal_return,
                "reversal_directional_basis_change_5m_bps": reversal_basis,
                "pre_attack_open_interest": _oi(data, attack_index - 15),
                "attack_open_interest": _oi(data, attack_index),
                "confirmation_open_interest": _oi(data, index),
                "inventory_route": route,
                "compiler": "candidate-04-post-event-inventory-resolution",
            }
            intents.append(
                Intent(
                    scenario=route,
                    side=trade_side,
                    signal_index=index,
                    entry_index=index + 1,
                    stop_level=stop,
                    event_indices=(attack_index, index),
                    details=details,
                )
            )
            if route == LIQUIDATION_REVERSAL:
                counts["liquidation_reversals"] += 1
            else:
                counts["trapped_inventory_reversals"] += 1
            last_signal = index
            confirmed = True
            break

        if not had_effort:
            counts["no_persistent_attack_effort"] += 1
        elif not confirmed:
            counts["no_exact_reclaim"] += 1

    return intents, counts


def informed_event_state(
    data: pd.DataFrame,
    index: int,
    thresholds: Thresholds,
) -> tuple[int, dict[str, float]] | None:
    row = data.iloc[index]
    flow = finite(row["flow_300s"])
    side = 1 if flow > 0.0 else -1 if flow < 0.0 else 0
    if side == 0:
        return None
    values = {
        "absolute_flow_300s": abs(flow),
        "flow_cutoff": finite(thresholds.abs_flow_300_q75.iloc[index]),
        "directional_return_300s_bps": side * finite(row["ret_300s_bps"]),
        "return_cutoff": finite(thresholds.abs_return_300_q65.iloc[index]),
        "persistence_300s": finite(row["flow_sign_persistence_300s"]),
        "persistence_cutoff": finite(thresholds.persistence_300_q60.iloc[index]),
        "efficiency_300s": finite(row["eff_300s"]),
        "efficiency_cutoff": finite(thresholds.efficiency_300_q60.iloc[index]),
        "notional_burst_60s": finite(row["notional_burst_60s"]),
        "notional_cutoff": finite(thresholds.notional_burst_60_q65.iloc[index]),
        "directional_basis_change_15m_bps": side
        * finite(row["basis_change_15m"]),
    }
    if not all(math.isfinite(value) for value in values.values()):
        return None
    pre_oi = _oi(data, index - 15)
    event_oi = _oi(data, index)
    oi_cutoff = finite(thresholds.positive_oi_step_median.iloc[index])
    if not (
        math.isfinite(pre_oi)
        and pre_oi > 0.0
        and math.isfinite(event_oi)
        and math.isfinite(oi_cutoff)
        and oi_cutoff > 0.0
    ):
        return None
    oi_change = event_oi / pre_oi - 1.0
    values["open_interest_change_15m"] = oi_change
    values["positive_oi_step_cutoff"] = oi_cutoff
    passed = (
        values["absolute_flow_300s"] >= values["flow_cutoff"]
        and values["directional_return_300s_bps"] >= values["return_cutoff"]
        and values["persistence_300s"] >= values["persistence_cutoff"]
        and values["efficiency_300s"] >= values["efficiency_cutoff"]
        and values["notional_burst_60s"] >= values["notional_cutoff"]
        and values["directional_basis_change_15m_bps"] > 0.0
        and oi_change >= oi_cutoff
    )
    return (side, values) if passed else None


def detect_informed_continuations(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    thresholds: Thresholds,
) -> tuple[list[Intent], dict[str, int]]:
    states = [informed_event_state(data, index, thresholds) for index in range(len(data))]
    intents: list[Intent] = []
    counts = {
        "event_rows": sum(item is not None for item in states),
        "event_onsets": 0,
        "insufficient_displacement": 0,
        "no_bounded_pullback": 0,
        "counter_effort_too_large": 0,
        "inventory_not_retained": 0,
        "no_efficient_resumption": 0,
        "confirmed_continuations": 0,
    }
    last_signal = -10**12

    for event_index, state in enumerate(states):
        if state is None:
            continue
        previous = states[event_index - 1] if event_index > 0 else None
        if previous is not None and int(previous[0]) == int(state[0]):
            continue
        if data.index[event_index] < evaluation_start:
            continue
        if data.index[event_index] > evaluation_end:
            break
        if event_index - last_signal < SIGNAL_COOLDOWN_BARS:
            continue
        counts["event_onsets"] += 1
        side, event_details = state
        origin_index = event_index - EVENT_LOOKBACK_BARS
        if origin_index < 0:
            continue
        origin = finite(data["close"].iloc[origin_index])
        event_close = finite(data["close"].iloc[event_index])
        atr = finite(data["atr"].iloc[event_index])
        displacement = side * (event_close - origin)
        if not (
            math.isfinite(displacement)
            and math.isfinite(atr)
            and atr > 0.0
            and displacement >= 0.60 * atr
        ):
            counts["insufficient_displacement"] += 1
            continue

        event_effort = abs(_directional_effort(data.iloc[event_index], side, 300))
        event_oi = _oi(data, event_index)
        pullback_index: int | None = None
        pullback_retracement = float("nan")
        upper = min(event_index + PULLBACK_MAX_BARS, len(data) - 2)
        for index in range(event_index + 1, upper + 1):
            close = finite(data["close"].iloc[index])
            retracement = side * (event_close - close) / displacement
            if retracement > PULLBACK_MAX_RETRACE:
                break
            if retracement < PULLBACK_MIN_RETRACE:
                continue
            row = data.iloc[index]
            if not (
                -side * finite(row["flow_60s"]) > 0.0
                and -side * finite(row["ret_60s_bps"]) > 0.0
            ):
                continue
            segment = data.iloc[event_index + 1 : index + 1]
            counter_effort = sum(
                max(_directional_effort(item, -side), 0.0)
                for _, item in segment.iterrows()
                if math.isfinite(_directional_effort(item, -side))
            )
            if not (
                math.isfinite(event_effort)
                and event_effort > 0.0
                and counter_effort <= MAX_COUNTER_EFFORT_FRACTION * event_effort
            ):
                counts["counter_effort_too_large"] += 1
                break
            current_oi = _oi(data, index)
            if not (
                math.isfinite(event_oi)
                and math.isfinite(current_oi)
                and current_oi >= MIN_INVENTORY_RETENTION * event_oi
            ):
                counts["inventory_not_retained"] += 1
                break
            if side * (close - origin) <= 0.0:
                continue
            pullback_index = index
            pullback_retracement = retracement
            break

        if pullback_index is None:
            counts["no_bounded_pullback"] += 1
            continue

        pullback_segment = data.iloc[event_index + 1 : pullback_index + 1]
        structure = finite(
            pullback_segment["high"].max()
            if side > 0
            else pullback_segment["low"].min()
        )
        confirm_upper = min(pullback_index + RESUMPTION_MAX_BARS, len(data) - 2)
        confirmed = False
        for index in range(pullback_index + 1, confirm_upper + 1):
            if data.index[index] > evaluation_end:
                break
            row = data.iloc[index]
            close = finite(row["close"])
            structure_broken = close > structure if side > 0 else close < structure
            if not structure_broken:
                continue
            flow = side * finite(row["flow_60s"])
            return_bps = side * finite(row["ret_60s_bps"])
            basis = side * finite(row["basis_change_5m"])
            efficiency = finite(row["eff_60s"])
            acceleration = side * finite(row["flow_accel_15_vs_prior45"])
            flow_cutoff = 0.50 * finite(thresholds.abs_flow_60_q70.iloc[index])
            efficiency_cutoff = finite(thresholds.efficiency_60_q50.iloc[index])
            if not all(
                math.isfinite(value)
                for value in (
                    flow,
                    return_bps,
                    basis,
                    efficiency,
                    acceleration,
                    flow_cutoff,
                    efficiency_cutoff,
                )
            ):
                continue
            if not (
                flow >= max(0.10, flow_cutoff)
                and return_bps > 0.0
                and basis > 0.0
                and efficiency >= efficiency_cutoff
                and acceleration > 0.0
                and _oi(data, index) >= _oi(data, pullback_index)
            ):
                continue
            stop = _response_stop(
                data,
                event_index + 1,
                index,
                side,
                impact_parameters,
            )
            if not math.isfinite(stop) or side * (close - stop) <= 0.0:
                continue
            details = {
                **event_details,
                "event_origin_index": origin_index,
                "event_index": event_index,
                "event_displacement_atr": displacement / atr,
                "pullback_index": pullback_index,
                "pullback_retracement_fraction": pullback_retracement,
                "pullback_structure": structure,
                "resumption_index": index,
                "resumption_directional_flow_60s": flow,
                "resumption_directional_return_60s_bps": return_bps,
                "resumption_directional_basis_change_5m_bps": basis,
                "resumption_efficiency_60s": efficiency,
                "resumption_flow_acceleration": acceleration,
                "compiler": "candidate-04-post-event-inventory-resolution",
            }
            intents.append(
                Intent(
                    scenario=INFORMED_CONTINUATION,
                    side=side,
                    signal_index=index,
                    entry_index=index + 1,
                    stop_level=stop,
                    event_indices=(
                        origin_index,
                        event_index,
                        pullback_index,
                        index,
                    ),
                    details=details,
                )
            )
            counts["confirmed_continuations"] += 1
            last_signal = index
            confirmed = True
            break
        if not confirmed:
            counts["no_efficient_resumption"] += 1

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
    thresholds = build_thresholds(data, config)
    reversals, reversal_counts = detect_post_attack_reversals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        thresholds,
    )
    continuations, continuation_counts = detect_informed_continuations(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        thresholds,
    )
    priority = {
        LIQUIDATION_REVERSAL: 0,
        TRAPPED_INVENTORY_REVERSAL: 1,
        INFORMED_CONTINUATION: 2,
    }
    combined = sorted(
        [*reversals, *continuations],
        key=lambda item: (int(item.signal_index), priority[str(item.scenario)]),
    )
    unique: list[Intent] = []
    seen: set[int] = set()
    duplicate_bars = 0
    for intent in combined:
        index = int(intent.signal_index)
        if index in seen:
            duplicate_bars += 1
            continue
        seen.add(index)
        unique.append(intent)
    return unique, {
        "candidate": "candidate-04-v34-post-event-inventory-resolution",
        "compiler": "candidate-04-post-event-inventory-resolution",
        "raw_routed_signals": len(combined),
        "unique_signal_bars": len(unique),
        "duplicate_signal_bars": duplicate_bars,
        "route_counts": {
            "post_attack_reversal": reversal_counts,
            "informed_continuation": continuation_counts,
        },
        "scenario_contract": {
            "liquidation_reversal": (
                "tail external-pool attack, persistent same-side aggressor effort, "
                "exact reclaim, aligned reversal and attack-time OI contraction"
            ),
            "trapped_inventory_reversal": (
                "tail external-pool attack creates material OI, persistent effort "
                "fails, exact reclaim and created OI contracts before confirmation"
            ),
            "continuation": (
                "efficient five-minute flow creates material OI, bounded weak "
                "counter-flow retains inventory, then efficient accelerating BOS"
            ),
            "distribution_boundaries": "shifted and past-only",
            "execution": "NautilusTrader BacktestNode only",
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
