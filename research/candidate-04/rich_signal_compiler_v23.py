#!/usr/bin/env python3
"""V23 causal rich-state compiler: parent-auction shock routing.

This compiler still emits scenario intents only. NautilusTrader remains the
sole owner of orders, fills, positions, fees, PnL and NAV.

V23 replaces activity buckets with four complete market states:

1. Normal-basis failed-auction resumption (unchanged, structurally profitable).
2. Normal-basis orderly inventory displacement. Five-minute acceptance must
   occur while the four-hour auction path is below its shifted prior median and
   futures basis remains inside its shifted central 80% band. This distinguishes
   orderly repricing from a climactic/crowded continuation.
3. Negative-basis failed-auction continuation. The terminal acceptance minute
   must itself be a shifted q80 directional return event, rather than merely
   closing beyond the rejected sweep.
4. Impact shock routing. A failed price-discovery reversal is allowed when it
   resumes the pre-shock 480-minute parent direction, or when the shock is larger
   than the opposing parent displacement. If a shock instead agrees with a
   dominant parent auction, the immediate fade is rejected; continuation is
   admitted only after the origin is reaccepted within three completed minutes
   with aligned flow, return and efficient displacement.

Every rolling threshold is shifted. Parent state ends before the shock, so the
shock cannot manufacture its own higher-timeframe bias.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401  # installs warmup-aware loader


Intent = v22.Intent


def _copy_intent(parent: Intent, scenario: str, details: dict[str, Any]) -> Intent:
    return Intent(
        scenario=scenario,
        side=parent.side,
        signal_index=parent.signal_index,
        entry_index=parent.entry_index,
        stop_level=parent.stop_level,
        event_indices=parent.event_indices,
        details=details,
    )


def _shifted_quantile(
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


def _impact_parent_return_bps(
    close: pd.Series,
    shock_index: int,
    bars: int = 480,
) -> float:
    if shock_index <= bars:
        return float("nan")
    parent_end = float(close.iloc[shock_index - 1])
    parent_start = float(close.iloc[shock_index - bars - 1])
    if not all(math.isfinite(value) and value > 0.0 for value in (parent_end, parent_start)):
        return float("nan")
    return (parent_end / parent_start - 1.0) * 10_000.0


def _parent_shock_continuation(
    data: pd.DataFrame,
    parent: Intent,
    impact_parameters: Any,
    parent_return_bps: float,
) -> Intent | None:
    details = dict(parent.details)
    shock_side = -int(parent.side)
    origin = float(details["origin"])
    shock_index = int(details["shock_index"])
    confirmation_index = int(parent.signal_index)
    upper = min(
        confirmation_index + int(impact_parameters.confirmation_minutes),
        len(data) - 2,
    )
    for index in range(confirmation_index + 1, upper + 1):
        row = data.iloc[index]
        close = float(row["close"])
        reaccepted = close > origin if shock_side > 0 else close < origin
        if not reaccepted:
            continue
        flow = float(row["flow_60s"])
        signed_return = float(row["ret_60s_bps"])
        efficiency = float(row["eff_60s"])
        if not all(math.isfinite(value) for value in (flow, signed_return, efficiency)):
            continue
        if not (
            shock_side * flow > 0.0
            and shock_side * signed_return > 0.0
            and efficiency >= float(impact_parameters.minimum_efficiency_60s)
        ):
            continue

        segment = data.iloc[shock_index : index + 1]
        atr = float(row["atr"])
        if not math.isfinite(atr) or atr <= 0.0:
            return None
        extreme = (
            float(segment["low"].min())
            if shock_side > 0
            else float(segment["high"].max())
        )
        stop = extreme - shock_side * float(impact_parameters.stop_buffer_atr) * atr
        routed_details = {
            **details,
            "parent_auction_480m_return_bps": parent_return_bps,
            "parent_auction_side": shock_side,
            "impact_shock_side": shock_side,
            "impact_fade_side": int(parent.side),
            "origin_reaccept_index": index,
            "origin_reaccept_delay_minutes": index - confirmation_index,
            "origin_reaccept_flow_60s": flow,
            "origin_reaccept_return_60s_bps": signed_return,
            "origin_reaccept_efficiency_60s": efficiency,
            "compiler": "candidate-04-v23",
        }
        return Intent(
            scenario="PARENT_AUCTION_SHOCK_REACCEPTANCE",
            side=shock_side,
            signal_index=index,
            entry_index=index + 1,
            stop_level=stop,
            event_indices=tuple(parent.event_indices) + (index,),
            details=routed_details,
        )
    return None


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    del router
    close = data["close"].astype(float)
    one_minute_path = close.pct_change(fill_method=None).abs()
    path_240 = one_minute_path.rolling(240, min_periods=240).sum() * 10_000.0

    window = int(config.stress_inventory_quantile_window_minutes)
    minimum = int(config.stress_inventory_quantile_min_periods)
    path_median = _shifted_quantile(path_240, 0.50, window, minimum)
    basis = data["trade_index_basis_bps"].astype(float)
    basis_q10 = _shifted_quantile(basis, 0.10, window, minimum)
    basis_q90 = _shifted_quantile(basis, 0.90, window, minimum)
    abs_return_q80 = _shifted_quantile(
        data["ret_60s_bps"].astype(float).abs(),
        0.80,
        int(impact_parameters.quantile_window_minutes),
        int(impact_parameters.quantile_min_periods),
    )

    swing, _ = v22.v9.v8.v7.v6.detect_swing_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )
    v22.v9.v8.detect_mesoscale_inventory_intents.original_detector = (
        v22.v9.v8.v7.v6.detect_trend_intents
    )
    trend, _ = v22.v9.v8.detect_mesoscale_inventory_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )
    v22.v9.filter_reversal_failure_intents.original_detector = (
        v22.v9.v8.v7.v6.detect_stress_failure_intents
    )
    stress_failure, _ = v22.v9.filter_reversal_failure_intents(
        data,
        swing,
        config,
    )
    impact, _ = v22.v10.detect_impact_exhaustion_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )

    routed: list[Intent] = []
    counts = {
        "normal_failed_auction": 0,
        "orderly_inventory": 0,
        "normal_inventory_climactic_rejected": 0,
        "stress_tail_acceptance": 0,
        "stress_weak_terminal_rejected": 0,
        "parent_aligned_impact_reversal": 0,
        "parent_shock_reacceptance": 0,
        "dominant_parent_fade_rejected": 0,
    }

    for parent in swing:
        index = int(parent.signal_index)
        regime = float(v22.v9.v8.v7.v6.basis_regime(data, index, config))
        if regime < float(config.basis_stress_threshold_bps):
            continue
        routed.append(
            _copy_intent(
                parent,
                "NORMAL_FAILED_AUCTION_RESUMPTION",
                {
                    **parent.details,
                    "basis_regime_bps": regime,
                    "trade_index_basis_bps": float(basis.iloc[index]),
                    "compiler": "candidate-04-v23",
                },
            ),
        )
        counts["normal_failed_auction"] += 1

    oi_change = data["oi_change_xday_15m"].astype(float)
    for parent in trend:
        index = int(parent.signal_index)
        regime = float(v22.v9.v8.v7.v6.basis_regime(data, index, config))
        if regime < float(config.basis_stress_threshold_bps):
            continue
        values = (
            float(path_240.iloc[index]),
            float(path_median.iloc[index]),
            float(basis.iloc[index]),
            float(basis_q10.iloc[index]),
            float(basis_q90.iloc[index]),
            float(oi_change.iloc[index]),
        )
        finite = all(math.isfinite(value) for value in values)
        orderly = (
            finite
            and values[0] <= values[1]
            and values[3] <= values[2] <= values[4]
        )
        details = {
            **parent.details,
            "basis_regime_bps": regime,
            "auction_path_240m_bps": values[0],
            "past_only_path_240m_median_bps": values[1],
            "trade_index_basis_bps": values[2],
            "past_only_basis_q10_bps": values[3],
            "past_only_basis_q90_bps": values[4],
            "raw_oi_change_15m": values[5],
            "orderly_parent_auction": orderly,
            "compiler": "candidate-04-v23",
        }
        if orderly:
            routed.append(
                _copy_intent(parent, "ORDERLY_INVENTORY_DISPLACEMENT", details),
            )
            counts["orderly_inventory"] += 1
        else:
            counts["normal_inventory_climactic_rejected"] += 1

    for parent in stress_failure:
        index = int(parent.signal_index)
        current_basis = float(basis.iloc[index])
        directional_return = int(parent.side) * float(data["ret_60s_bps"].iloc[index])
        cutoff = float(abs_return_q80.iloc[index])
        passed = (
            math.isfinite(current_basis)
            and current_basis < 0.0
            and math.isfinite(directional_return)
            and math.isfinite(cutoff)
            and directional_return >= cutoff
        )
        details = {
            **parent.details,
            "trade_index_basis_bps": current_basis,
            "terminal_directional_return_60s_bps": directional_return,
            "past_only_absolute_return_q80_bps": cutoff,
            "terminal_tail_acceptance": passed,
            "compiler": "candidate-04-v23",
        }
        if passed:
            routed.append(
                _copy_intent(
                    parent,
                    "TAIL_CONFIRMED_STRESS_FAILED_AUCTION",
                    details,
                ),
            )
            counts["stress_tail_acceptance"] += 1
        else:
            counts["stress_weak_terminal_rejected"] += 1

    for parent in impact:
        index = int(parent.signal_index)
        current_basis = float(basis.iloc[index])
        if not math.isfinite(current_basis) or current_basis >= 0.0:
            continue
        shock_index = int(parent.details["shock_index"])
        parent_return = _impact_parent_return_bps(close, shock_index)
        shock_return = float(parent.details["absolute_return_bps"])
        trade_side = int(parent.side)
        parent_supports_fade = (
            math.isfinite(parent_return)
            and (
                trade_side * parent_return >= 0.0
                or abs(parent_return) < shock_return
            )
        )
        details = {
            **parent.details,
            "trade_index_basis_bps": current_basis,
            "pre_shock_parent_480m_return_bps": parent_return,
            "impact_absolute_return_bps": shock_return,
            "trade_side_parent_return_bps": trade_side * parent_return,
            "parent_supports_fade": parent_supports_fade,
            "compiler": "candidate-04-v23",
        }
        if parent_supports_fade:
            routed.append(
                _copy_intent(
                    parent,
                    "PARENT_ALIGNED_LIQUIDATION_REVERSAL",
                    details,
                ),
            )
            counts["parent_aligned_impact_reversal"] += 1
            continue

        continuation = _parent_shock_continuation(
            data,
            parent,
            impact_parameters,
            parent_return,
        )
        if continuation is not None:
            routed.append(continuation)
            counts["parent_shock_reacceptance"] += 1
        else:
            counts["dominant_parent_fade_rejected"] += 1

    priority = {
        "PARENT_ALIGNED_LIQUIDATION_REVERSAL": 0,
        "PARENT_AUCTION_SHOCK_REACCEPTANCE": 1,
        "NORMAL_FAILED_AUCTION_RESUMPTION": 2,
        "TAIL_CONFIRMED_STRESS_FAILED_AUCTION": 3,
        "ORDERLY_INVENTORY_DISPLACEMENT": 4,
    }
    routed.sort(
        key=lambda item: (
            int(item.signal_index),
            priority.get(item.scenario, 99),
        ),
    )
    unique: list[Intent] = []
    seen: set[int] = set()
    for intent in routed:
        index = int(intent.signal_index)
        if index in seen:
            continue
        seen.add(index)
        unique.append(intent)

    return unique, {
        "raw_routed_signals": len(routed),
        "unique_signal_bars": len(unique),
        "route_counts": counts,
        "impact_parameters": asdict(impact_parameters),
        "compiler": "candidate-04-v23",
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
