#!/usr/bin/env python3
"""Order-flow impact regime router for causal SMC/ICT scenarios.

This candidate moves beyond a single reclaim bar. It treats executed order flow
as a latent regime whose price impact determines which SMC/ICT scenario exists.
Two mechanisms are mutually exclusive:

1. informed inventory regime pullback/resumption
   * persistent five-minute executed flow, price displacement and basis move;
   * exchange open interest expands during the displacement;
   * price retraces into the displacement without materially liquidating the
     new inventory; and
   * a completed non-climactic terminal bar breaks the pullback structure with
     flow, return and basis aligned.

2. external-liquidity absorbed-flow reversal
   * the first meaningful penetration of a causal external pivot pool carries
     tail executed flow and notional;
   * price impact efficiency is low while absorption and reversal-side displayed
     depth replenishment are high;
   * open interest does not expand; and
   * a later completed bar reclaims the exact pool with flow, return and basis
     aligned in the reversal direction.

Every distributional boundary is shifted and past-only. The compiler never
matches orders, sizes risk, computes PnL or updates NAV. NautilusTrader remains
the sole owner of targets, orders, fills, costs, positions, margin, liquidation
and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v24 as v24


Intent = v22.Intent

CONTINUATION_SCENARIO = "INFORMED_FLOW_REGIME_PULLBACK_RESUMPTION"
REVERSAL_SCENARIO = "EXTERNAL_LIQUIDITY_ABSORBED_FLOW_REVERSAL"

FLOW_REGIME_QUANTILE = 0.80
RETURN_REGIME_QUANTILE = 0.70
PERSISTENCE_QUANTILE = 0.65
EFFICIENCY_QUANTILE = 0.65
NOTIONAL_QUANTILE = 0.70
ABSORPTION_QUANTILE = 0.80
LOW_IMPACT_QUANTILE = 0.35

REGIME_LOOKBACK_BARS = 5
PULLBACK_MAX_BARS = 30
RESUMPTION_MAX_BARS = 15
RETRACEMENT_MIN = 0.20
RETRACEMENT_MAX = 1.00
REVERSAL_CONFIRM_BARS = 5
SIGNAL_COOLDOWN_BARS = 30
DEPTH_BANDS = (1, 2, 3, 4, 5)
DEPTH_CHANGE_SECONDS = 60
MAX_DEPTH_SNAPSHOT_AGE_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class PastOnlyThresholds:
    flow_300s_q80: pd.Series
    abs_return_300s_q70: pd.Series
    persistence_300s_q65: pd.Series
    efficiency_300s_q65: pd.Series
    notional_burst_60s_q70: pd.Series
    flow_60s_q80: pd.Series
    abs_return_60s_q80: pd.Series
    absorption_60s_q80: pd.Series
    efficiency_60s_q35: pd.Series
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
    window: int,
    minimum: int,
) -> pd.Series:
    return (
        series.astype(float)
        .replace([math.inf, -math.inf], float("nan"))
        .shift(1)
        .rolling(window, min_periods=minimum)
        .quantile(quantile)
    )


def build_thresholds(data: pd.DataFrame, config: Any) -> PastOnlyThresholds:
    window = int(config.stress_inventory_quantile_window_minutes)
    minimum = int(config.stress_inventory_quantile_min_periods)
    open_interest = data["metric_sum_open_interest"].astype(float)
    positive_oi_step = open_interest.pct_change(fill_method=None).where(
        lambda value: value > 0.0
    )
    return PastOnlyThresholds(
        flow_300s_q80=shifted_quantile(
            data["flow_300s"].astype(float).abs(),
            FLOW_REGIME_QUANTILE,
            window,
            minimum,
        ),
        abs_return_300s_q70=shifted_quantile(
            data["ret_300s_bps"].astype(float).abs(),
            RETURN_REGIME_QUANTILE,
            window,
            minimum,
        ),
        persistence_300s_q65=shifted_quantile(
            data["flow_sign_persistence_300s"],
            PERSISTENCE_QUANTILE,
            window,
            minimum,
        ),
        efficiency_300s_q65=shifted_quantile(
            data["eff_300s"],
            EFFICIENCY_QUANTILE,
            window,
            minimum,
        ),
        notional_burst_60s_q70=shifted_quantile(
            data["notional_burst_60s"],
            NOTIONAL_QUANTILE,
            window,
            minimum,
        ),
        flow_60s_q80=shifted_quantile(
            data["flow_60s"].astype(float).abs(),
            FLOW_REGIME_QUANTILE,
            window,
            minimum,
        ),
        abs_return_60s_q80=shifted_quantile(
            data["ret_60s_bps"].astype(float).abs(),
            FLOW_REGIME_QUANTILE,
            window,
            minimum,
        ),
        absorption_60s_q80=shifted_quantile(
            data["absorption_60s"],
            ABSORPTION_QUANTILE,
            window,
            minimum,
        ),
        efficiency_60s_q35=shifted_quantile(
            data["eff_60s"],
            LOW_IMPACT_QUANTILE,
            window,
            minimum,
        ),
        positive_oi_step_median=shifted_quantile(
            positive_oi_step,
            0.50,
            window,
            max(30, minimum // 4),
        ),
    )


def directional_depth_replenishment(row: pd.Series, trade_side: int) -> float:
    """Positive means displayed depth replenishes on the intended trade side."""

    if trade_side not in (-1, 1):
        return float("nan")
    values: list[float] = []
    for band in DEPTH_BANDS:
        bid = finite(row[f"bid_chg_{band}_{DEPTH_CHANGE_SECONDS}s"])
        ask = finite(row[f"ask_chg_{band}_{DEPTH_CHANGE_SECONDS}s"])
        if not (math.isfinite(bid) and math.isfinite(ask)):
            continue
        values.append(trade_side * (bid - ask))
    return median(values) if values else float("nan")


def _oi(data: pd.DataFrame, index: int) -> float:
    if not 0 <= index < len(data):
        return float("nan")
    return finite(data["metric_sum_open_interest"].iloc[index])


def flow_regime_state(
    data: pd.DataFrame,
    index: int,
    thresholds: PastOnlyThresholds,
) -> tuple[int, dict[str, float]] | None:
    row = data.iloc[index]
    flow_300s = finite(row["flow_300s"])
    side = 1 if flow_300s > 0.0 else -1 if flow_300s < 0.0 else 0
    if side == 0:
        return None
    values = {
        "absolute_flow_300s": abs(flow_300s),
        "past_only_flow_300s_q80": finite(thresholds.flow_300s_q80.iloc[index]),
        "directional_return_300s_bps": side * finite(row["ret_300s_bps"]),
        "past_only_abs_return_300s_q70_bps": finite(
            thresholds.abs_return_300s_q70.iloc[index]
        ),
        "flow_sign_persistence_300s": finite(
            row["flow_sign_persistence_300s"]
        ),
        "past_only_persistence_300s_q65": finite(
            thresholds.persistence_300s_q65.iloc[index]
        ),
        "efficiency_300s": finite(row["eff_300s"]),
        "past_only_efficiency_300s_q65": finite(
            thresholds.efficiency_300s_q65.iloc[index]
        ),
        "notional_burst_60s": finite(row["notional_burst_60s"]),
        "past_only_notional_burst_60s_q70": finite(
            thresholds.notional_burst_60s_q70.iloc[index]
        ),
        "directional_basis_change_15m_bps": side
        * finite(row["basis_change_15m"]),
    }
    if not all(math.isfinite(value) for value in values.values()):
        return None
    onset_oi = _oi(data, index)
    prior_oi = _oi(data, index - 15)
    if not (
        math.isfinite(onset_oi)
        and math.isfinite(prior_oi)
        and prior_oi > 0.0
    ):
        return None
    oi_change = onset_oi / prior_oi - 1.0
    oi_cutoff = finite(thresholds.positive_oi_step_median.iloc[index])
    values.update(
        {
            "open_interest_change_15m": oi_change,
            "past_only_positive_oi_step_median": oi_cutoff,
        }
    )
    passed = (
        values["absolute_flow_300s"] >= values["past_only_flow_300s_q80"]
        and values["directional_return_300s_bps"]
        >= values["past_only_abs_return_300s_q70_bps"]
        and values["flow_sign_persistence_300s"]
        >= values["past_only_persistence_300s_q65"]
        and values["efficiency_300s"]
        >= values["past_only_efficiency_300s_q65"]
        and values["notional_burst_60s"]
        >= values["past_only_notional_burst_60s_q70"]
        and values["directional_basis_change_15m_bps"] > 0.0
        and math.isfinite(oi_cutoff)
        and oi_cutoff > 0.0
        and oi_change >= oi_cutoff
    )
    return (side, values) if passed else None


def _continuation_stop(
    data: pd.DataFrame,
    pullback_index: int,
    resume_index: int,
    side: int,
    impact_parameters: Any,
) -> float:
    segment = data.iloc[pullback_index : resume_index + 1]
    atr = finite(data["atr"].iloc[resume_index])
    if segment.empty or not math.isfinite(atr) or atr <= 0.0:
        return float("nan")
    extreme = finite(
        segment["low"].min() if side > 0 else segment["high"].max()
    )
    return extreme - side * float(impact_parameters.stop_buffer_atr) * atr


def detect_informed_flow_regime_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    thresholds: PastOnlyThresholds,
) -> tuple[list[Intent], dict[str, int]]:
    state = [flow_regime_state(data, index, thresholds) for index in range(len(data))]
    intents: list[Intent] = []
    counts = {
        "regime_rows": sum(item is not None for item in state),
        "regime_onsets": 0,
        "insufficient_displacement": 0,
        "no_pullback": 0,
        "pullback_inventory_liquidated": 0,
        "no_resumption": 0,
        "confirmed_resumption": 0,
    }
    last_signal = -10**12
    for onset_index, current in enumerate(state):
        if current is None:
            continue
        previous = state[onset_index - 1] if onset_index > 0 else None
        if previous is not None and int(previous[0]) == int(current[0]):
            continue
        timestamp = data.index[onset_index]
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        if onset_index - last_signal < SIGNAL_COOLDOWN_BARS:
            continue
        counts["regime_onsets"] += 1
        side, regime_details = current
        origin_index = onset_index - REGIME_LOOKBACK_BARS
        if origin_index < 0:
            continue
        origin = finite(data["close"].iloc[origin_index])
        event_close = finite(data["close"].iloc[onset_index])
        atr = finite(data["atr"].iloc[onset_index])
        displacement = side * (event_close - origin)
        if not (
            math.isfinite(displacement)
            and math.isfinite(atr)
            and atr > 0.0
            and displacement >= 0.80 * atr
        ):
            counts["insufficient_displacement"] += 1
            continue

        onset_oi = _oi(data, onset_index)
        pullback_index: int | None = None
        pullback_retracement = float("nan")
        pullback_upper = min(onset_index + PULLBACK_MAX_BARS, len(data) - 2)
        for index in range(onset_index + 1, pullback_upper + 1):
            close = finite(data["close"].iloc[index])
            retracement = side * (event_close - close) / displacement
            if retracement > RETRACEMENT_MAX:
                break
            if retracement < RETRACEMENT_MIN:
                continue
            row = data.iloc[index]
            counter_flow = -side * finite(row["flow_60s"])
            counter_return = -side * finite(row["ret_60s_bps"])
            if not (
                math.isfinite(counter_flow)
                and math.isfinite(counter_return)
                and counter_flow > 0.0
                and counter_return > 0.0
            ):
                continue
            pullback_oi = _oi(data, index)
            if not (
                math.isfinite(onset_oi)
                and math.isfinite(pullback_oi)
                and pullback_oi >= onset_oi * 0.998
            ):
                counts["pullback_inventory_liquidated"] += 1
                break
            pullback_index = index
            pullback_retracement = retracement
            break
        if pullback_index is None:
            counts["no_pullback"] += 1
            continue

        pre_pullback = data.iloc[onset_index : pullback_index + 1]
        structure = finite(
            pre_pullback["high"].max()
            if side > 0
            else pre_pullback["low"].min()
        )
        resume_upper = min(pullback_index + RESUMPTION_MAX_BARS, len(data) - 2)
        confirmed = False
        for resume_index in range(pullback_index + 1, resume_upper + 1):
            if data.index[resume_index] > evaluation_end:
                break
            row = data.iloc[resume_index]
            close = finite(row["close"])
            structure_broken = close > structure if side > 0 else close < structure
            if not structure_broken:
                continue
            flow = side * finite(row["flow_60s"])
            return_bps = side * finite(row["ret_60s_bps"])
            basis = side * finite(row["basis_change_5m"])
            absorption = finite(row["absorption_60s"])
            efficiency = finite(row["eff_60s"])
            acceleration = side * finite(row["flow_accel_15_vs_prior45"])
            resume_oi = _oi(data, resume_index)
            values = (
                flow,
                return_bps,
                basis,
                absorption,
                efficiency,
                acceleration,
                resume_oi,
                onset_oi,
            )
            if not all(math.isfinite(value) for value in values):
                continue
            passed = (
                flow >= max(0.15, 0.50 * finite(thresholds.flow_60s_q80.iloc[resume_index]))
                and return_bps > 0.0
                and basis > 0.0
                and absorption >= float(config.trend_absorption_60s_min)
                and efficiency <= float(config.trend_efficiency_60s_max)
                and acceleration <= float(config.trend_flow_acceleration_max)
                and resume_oi >= onset_oi
            )
            if not passed:
                continue
            stop = _continuation_stop(
                data,
                pullback_index,
                resume_index,
                side,
                impact_parameters,
            )
            if not math.isfinite(stop) or side * (close - stop) <= 0.0:
                continue
            details = {
                **regime_details,
                "regime_onset_index": onset_index,
                "regime_origin_index": origin_index,
                "regime_displacement_atr": displacement / atr,
                "pullback_index": pullback_index,
                "pullback_retracement_fraction": pullback_retracement,
                "pullback_structure_level": structure,
                "resumption_index": resume_index,
                "resumption_directional_flow_60s": flow,
                "resumption_directional_return_60s_bps": return_bps,
                "resumption_directional_basis_change_5m_bps": basis,
                "resumption_absorption_60s": absorption,
                "resumption_efficiency_60s": efficiency,
                "resumption_flow_acceleration": acceleration,
                "compiler": "candidate-04-order-flow-impact-regime",
            }
            intents.append(
                Intent(
                    scenario=CONTINUATION_SCENARIO,
                    side=side,
                    signal_index=resume_index,
                    entry_index=resume_index + 1,
                    stop_level=stop,
                    event_indices=(
                        origin_index,
                        onset_index,
                        pullback_index,
                        resume_index,
                    ),
                    details=details,
                )
            )
            counts["confirmed_resumption"] += 1
            last_signal = resume_index
            confirmed = True
            break
        if not confirmed:
            counts["no_resumption"] += 1

    return intents, counts


def detect_absorbed_flow_reversal_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    thresholds: PastOnlyThresholds,
) -> tuple[list[Intent], dict[str, int]]:
    takes = v24.detect_external_pool_takes(data, config)
    intents: list[Intent] = []
    counts = {
        "eligible_pool_takes": sum(len(value) for value in takes.values()),
        "ambiguous_pool_takes": 0,
        "shock_not_tail": 0,
        "shock_not_absorbed": 0,
        "depth_not_replenished": 0,
        "inventory_expanded": 0,
        "no_reclaim": 0,
        "confirmed_reversal": 0,
    }
    last_signal = -10**12
    for shock_index, candidates in sorted(takes.items()):
        timestamp = data.index[shock_index]
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        if shock_index - last_signal < SIGNAL_COOLDOWN_BARS:
            continue
        ranked: list[tuple[Any, dict[str, float]]] = []
        row = data.iloc[shock_index]
        for take in candidates:
            shock_side = int(take.pool_side)
            shock_flow = shock_side * finite(row["flow_60s"])
            shock_return = shock_side * finite(row["ret_60s_bps"])
            notional = finite(row["notional_burst_60s"])
            absorption = finite(row["absorption_60s"])
            efficiency = finite(row["eff_60s"])
            tail_values = (
                shock_flow,
                shock_return,
                notional,
                absorption,
                efficiency,
                finite(thresholds.flow_60s_q80.iloc[shock_index]),
                finite(thresholds.notional_burst_60s_q70.iloc[shock_index]),
                finite(thresholds.absorption_60s_q80.iloc[shock_index]),
                finite(thresholds.efficiency_60s_q35.iloc[shock_index]),
            )
            if not all(math.isfinite(value) for value in tail_values):
                continue
            tail = (
                shock_flow >= tail_values[5]
                and shock_return > 0.0
                and notional >= tail_values[6]
            )
            if not tail:
                counts["shock_not_tail"] += 1
                continue
            absorbed = absorption >= tail_values[7] and efficiency <= tail_values[8]
            if not absorbed:
                counts["shock_not_absorbed"] += 1
                continue
            trade_side = int(take.trade_side)
            depth_age = finite(row["depth_snapshot_age_seconds"])
            replenishment = directional_depth_replenishment(row, trade_side)
            if not (
                math.isfinite(depth_age)
                and depth_age <= MAX_DEPTH_SNAPSHOT_AGE_SECONDS
                and math.isfinite(replenishment)
                and replenishment > 0.0
            ):
                counts["depth_not_replenished"] += 1
                continue
            current_oi = _oi(data, shock_index)
            prior_oi = _oi(data, shock_index - 15)
            oi_change = (
                current_oi / prior_oi - 1.0
                if math.isfinite(current_oi)
                and math.isfinite(prior_oi)
                and prior_oi > 0.0
                else float("nan")
            )
            if not math.isfinite(oi_change) or oi_change > 0.0:
                counts["inventory_expanded"] += 1
                continue
            ranked.append(
                (
                    take,
                    {
                        "shock_directional_flow_60s": shock_flow,
                        "shock_directional_return_60s_bps": shock_return,
                        "shock_notional_burst_60s": notional,
                        "shock_absorption_60s": absorption,
                        "shock_efficiency_60s": efficiency,
                        "reversal_side_depth_replenishment": replenishment,
                        "shock_open_interest_change_15m": oi_change,
                    },
                )
            )
        if not ranked:
            continue
        if len(ranked) > 1:
            counts["ambiguous_pool_takes"] += len(ranked) - 1
        take, shock_details = max(
            ranked,
            key=lambda item: (
                float(item[0].prominence_atr),
                int(item[0].age_bars),
                int(item[0].touches),
                float(item[0].penetration_atr),
            ),
        )
        trade_side = int(take.trade_side)
        upper = min(shock_index + REVERSAL_CONFIRM_BARS, len(data) - 2)
        confirmed = False
        for index in range(shock_index, upper + 1):
            if data.index[index] > evaluation_end:
                break
            candidate = data.iloc[index]
            close = finite(candidate["close"])
            reclaimed = (
                close < float(take.level)
                if int(take.pool_side) > 0
                else close > float(take.level)
            )
            if not reclaimed:
                continue
            flow = trade_side * finite(candidate["flow_60s"])
            return_bps = trade_side * finite(candidate["ret_60s_bps"])
            basis = trade_side * finite(candidate["basis_change_5m"])
            if not (
                math.isfinite(flow)
                and math.isfinite(return_bps)
                and math.isfinite(basis)
                and flow > 0.0
                and return_bps > 0.0
                and basis > 0.0
            ):
                continue
            segment = data.iloc[shock_index : index + 1]
            atr = finite(candidate["atr"])
            if not math.isfinite(atr) or atr <= 0.0:
                continue
            extreme = finite(
                segment["low"].min()
                if trade_side > 0
                else segment["high"].max()
            )
            stop = extreme - trade_side * float(impact_parameters.stop_buffer_atr) * atr
            if trade_side * (close - stop) <= 0.0:
                continue
            details = {
                **shock_details,
                "pool_id": int(take.pool_id),
                "pool_side": int(take.pool_side),
                "pool_level": float(take.level),
                "pool_extreme": float(take.extreme),
                "pool_penetration_atr": float(take.penetration_atr),
                "pool_age_bars": int(take.age_bars),
                "pool_prominence_atr": float(take.prominence_atr),
                "pool_touches": int(take.touches),
                "shock_index": shock_index,
                "reclaim_index": index,
                "reclaim_delay_bars": index - shock_index,
                "reversal_directional_flow_60s": flow,
                "reversal_directional_return_60s_bps": return_bps,
                "reversal_directional_basis_change_5m_bps": basis,
                "compiler": "candidate-04-order-flow-impact-regime",
            }
            intents.append(
                Intent(
                    scenario=REVERSAL_SCENARIO,
                    side=trade_side,
                    signal_index=index,
                    entry_index=index + 1,
                    stop_level=stop,
                    event_indices=(shock_index, index),
                    details=details,
                )
            )
            counts["confirmed_reversal"] += 1
            last_signal = index
            confirmed = True
            break
        if not confirmed:
            counts["no_reclaim"] += 1

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
    continuation, continuation_counts = detect_informed_flow_regime_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        thresholds,
    )
    reversal, reversal_counts = detect_absorbed_flow_reversal_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        thresholds,
    )
    priority = {REVERSAL_SCENARIO: 0, CONTINUATION_SCENARIO: 1}
    combined = sorted(
        [*continuation, *reversal],
        key=lambda item: (int(item.signal_index), priority[str(item.scenario)]),
    )
    unique: list[Intent] = []
    seen: set[int] = set()
    duplicate_signal_bars = 0
    for intent in combined:
        index = int(intent.signal_index)
        if index in seen:
            duplicate_signal_bars += 1
            continue
        seen.add(index)
        unique.append(intent)
    return unique, {
        "candidate": "candidate-04-v32-order-flow-impact-regime",
        "compiler": "candidate-04-order-flow-impact-regime",
        "raw_routed_signals": len(unique),
        "unique_signal_bars": len(unique),
        "duplicate_signal_bars": duplicate_signal_bars,
        "route_counts": {
            "informed_flow_regime": continuation_counts,
            "absorbed_external_flow": reversal_counts,
        },
        "scenario_contract": {
            "continuation": (
                "persistent 300-second executed-flow and price-impact regime, "
                "material OI creation, pullback without inventory liquidation, "
                "then non-climactic structure resumption"
            ),
            "reversal": (
                "first causal external-pool penetration with tail flow/notional, "
                "low price-impact efficiency, high absorption, reversal-side "
                "depth replenishment, no OI expansion and exact-pool reclaim"
            ),
            "distribution_boundaries": "shifted and past-only",
            "execution": "NautilusTrader BacktestNode only",
        },
        "constants": {
            "flow_regime_quantile": FLOW_REGIME_QUANTILE,
            "return_regime_quantile": RETURN_REGIME_QUANTILE,
            "persistence_quantile": PERSISTENCE_QUANTILE,
            "efficiency_quantile": EFFICIENCY_QUANTILE,
            "notional_quantile": NOTIONAL_QUANTILE,
            "absorption_quantile": ABSORPTION_QUANTILE,
            "low_impact_quantile": LOW_IMPACT_QUANTILE,
            "pullback_max_bars": PULLBACK_MAX_BARS,
            "resumption_max_bars": RESUMPTION_MAX_BARS,
            "reversal_confirmation_bars": REVERSAL_CONFIRM_BARS,
            "signal_cooldown_bars": SIGNAL_COOLDOWN_BARS,
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
