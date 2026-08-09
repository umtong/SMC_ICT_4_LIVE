#!/usr/bin/env python3
"""Component-cascade diagnostic for frozen v38 reversal attempts.

This module does not relax a trading rule. It decomposes the already frozen
failed-reversal continuation predicate into completed-bar observations so that a
zero-event result can be classified correctly: absent price reacceptance versus
price reacceptance lacking causal flow, efficiency, activity or book support.
It creates no orders, fills, positions, fees, account state or PnL.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any

import pandas as pd

import v38_failure_path_diagnostic as _base
import v38_failure_path_diagnostic_v2 as _v2


HORIZONS = _base.HORIZONS
_BASE_ANALYZE_CASE = _v2.analyze_case
_BASE_AGGREGATE = _v2.aggregate
_BASE_WRITE_JSON = _v2.write_json


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def _bar_components(
    row: pd.Series,
    *,
    continuation_side: int,
    sweep_extreme: float,
    sweep_atr: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])
    open_price = float(row["open"])
    flow_15s = _number(row["flow_15s"])
    flow_60s = _number(row["flow_60s"])
    efficiency = _number(row["efficiency_60s"])
    burst = _number(row["notional_burst"])
    bid_change = _number(row["bid_depth_change_1_1m"])
    ask_change = _number(row["ask_depth_change_1_1m"])
    relevant_depth_change = ask_change if continuation_side > 0 else bid_change
    span = max(high - low, 1e-12)
    close_location = (
        (close - low) / span
        if continuation_side > 0
        else (high - close) / span
    )
    outside_distance_atr = (
        continuation_side * (close - sweep_extreme) / sweep_atr
    )
    components = {
        "price_reaccepted": outside_distance_atr
        >= float(config["acceptance_close_atr"]),
        "directional_body": continuation_side * (close - open_price) > 0.0,
        "flow_15s_aligned": continuation_side * flow_15s
        >= float(config["acceptance_flow_min"]),
        "flow_60s_aligned": continuation_side * flow_60s
        >= float(config["acceptance_flow_min"]),
        "efficient": efficiency >= float(config["acceptance_efficiency_min"]),
        "active": burst >= float(config["sweep_min_notional_burst"]),
        "threatened_depth_withdrawn": -relevant_depth_change
        >= float(config["acceptance_depth_withdrawal_min"]),
        "close_location_confirmed": close_location
        >= float(config["acceptance_close_location"]),
    }
    ordered = (
        "price_reaccepted",
        "directional_body",
        "flow_15s_aligned",
        "flow_60s_aligned",
        "efficient",
        "active",
        "threatened_depth_withdrawn",
        "close_location_confirmed",
    )
    cumulative: dict[str, bool] = {}
    running = True
    for name in ordered:
        running = running and bool(components[name])
        cumulative[name] = running
    return {
        "ts_event": int(row["observed_time_ns"]),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "outside_distance_atr": outside_distance_atr,
        "flow_15s": flow_15s,
        "flow_60s": flow_60s,
        "efficiency_60s": efficiency,
        "notional_burst": burst,
        "relevant_depth_change_1m": relevant_depth_change,
        "close_location": close_location,
        "components": components,
        "cumulative_components": cumulative,
        "strict_reacceptance": all(components.values()),
    }


def _first_price_reacceptance_retest(
    selected: pd.DataFrame,
    *,
    first_index: int,
    continuation_side: int,
    sweep_extreme: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    expiry = min(
        len(selected) - 1,
        first_index + int(config["acceptance_retrace_bars"]),
    )
    for index in range(first_index + 1, expiry + 1):
        row = selected.iloc[index]
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        touched = low <= sweep_extreme <= high
        if touched:
            close_defended = (
                close > sweep_extreme
                if continuation_side > 0
                else close < sweep_extreme
            )
            flow_15s = _number(row["flow_15s"])
            depth = _number(row["depth_imbalance_1"])
            full_defense = _base.first_continuation_retest_response(
                continuation_side=continuation_side,
                sweep_extreme=sweep_extreme,
                high=high,
                low=low,
                close=close,
                flow_15s=flow_15s,
                depth_imbalance=depth,
                maximum_counterflow=float(config["acceptance_max_counterflow"]),
            )
            return {
                "result": (
                    "FIRST_PRICE_REACCEPTANCE_RETEST_FULLY_DEFENDED"
                    if full_defense
                    else "FIRST_PRICE_REACCEPTANCE_RETEST_CLOSE_DEFENDED"
                    if close_defended
                    else "FIRST_PRICE_REACCEPTANCE_RETEST_FAILED"
                ),
                "index": index,
                "ts_event": int(row["observed_time_ns"]),
                "high": high,
                "low": low,
                "close": close,
                "flow_15s": flow_15s,
                "depth_imbalance": depth,
                "close_defended": close_defended,
                "fully_defended": full_defense,
            }
        close_invalidated = (
            close < sweep_extreme
            if continuation_side > 0
            else close > sweep_extreme
        )
        if close_invalidated:
            return {
                "result": "PRICE_REACCEPTANCE_FAILED_BEFORE_RETEST",
                "index": index,
                "ts_event": int(row["observed_time_ns"]),
                "close": close,
                "close_defended": False,
                "fully_defended": False,
            }
    return {
        "result": "NO_FIRST_RETEST_WITHIN_EXISTING_WINDOW",
        "index": None,
        "ts_event": None,
        "close_defended": False,
        "fully_defended": False,
    }


def analyze_case(
    case: dict[str, Any],
    frame: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    result = _BASE_ANALYZE_CASE(case, frame, config)
    selected = _v2._selected_observation_frame(case, frame, config)
    continuation_side = int(result["continuation_side"])
    sweep_extreme = float(case["sweep_extreme"])
    sweep_atr = float(case["sweep_atr"])

    cascade_names = (
        "price_reaccepted",
        "directional_body",
        "flow_15s_aligned",
        "flow_60s_aligned",
        "efficient",
        "active",
        "threatened_depth_withdrawn",
        "close_location_confirmed",
    )
    cumulative_counts = {name: 0 for name in cascade_names}
    individual_counts = {name: 0 for name in cascade_names}
    price_bars: list[dict[str, Any]] = []
    maximum_extension = -math.inf
    first_price_index: int | None = None
    first_price: dict[str, Any] | None = None

    for index, row in selected.iterrows():
        observation = _bar_components(
            row,
            continuation_side=continuation_side,
            sweep_extreme=sweep_extreme,
            sweep_atr=sweep_atr,
            config=config,
        )
        for name in cascade_names:
            individual_counts[name] += int(observation["components"][name])
            cumulative_counts[name] += int(
                observation["cumulative_components"][name],
            )
        maximum_extension = max(
            maximum_extension,
            continuation_side
            * (
                (float(row["high"]) if continuation_side > 0 else float(row["low"]))
                - sweep_extreme
            )
            / sweep_atr,
        )
        if observation["components"]["price_reaccepted"]:
            if first_price_index is None:
                first_price_index = int(index)
                first_price = observation
            price_bars.append(observation)

    first_retest = None
    price_excursions = None
    structural_retest_excursions = None
    full_retest_excursions = None
    if first_price_index is not None:
        first_retest = _first_price_reacceptance_retest(
            selected,
            first_index=first_price_index,
            continuation_side=continuation_side,
            sweep_extreme=sweep_extreme,
            config=config,
        )
        price_excursions = _base.excursion_metrics(
            selected,
            start_index=first_price_index,
            continuation_side=continuation_side,
            reference_price=float(selected.iloc[first_price_index]["close"]),
            sweep_extreme=sweep_extreme,
            sweep_atr=sweep_atr,
        )
        retest_index = first_retest.get("index")
        if retest_index is not None and first_retest.get("close_defended"):
            structural_retest_excursions = _base.excursion_metrics(
                selected,
                start_index=int(retest_index),
                continuation_side=continuation_side,
                reference_price=float(selected.iloc[int(retest_index)]["close"]),
                sweep_extreme=sweep_extreme,
                sweep_atr=sweep_atr,
            )
        if retest_index is not None and first_retest.get("fully_defended"):
            full_retest_excursions = structural_retest_excursions

    result.update(
        {
            "component_cascade": {
                "ordered_components": list(cascade_names),
                "individual_bar_counts": individual_counts,
                "cumulative_bar_counts": cumulative_counts,
                "maximum_original_direction_extension_beyond_sweep_atr": (
                    maximum_extension if math.isfinite(maximum_extension) else None
                ),
                "first_price_reacceptance": first_price,
                "price_reacceptance_bars": len(price_bars),
                "first_price_reacceptance_during_original_position": (
                    bool(first_price)
                    and int(first_price["ts_event"])
                    <= int(case["position_close_ts"])
                ),
                "first_price_reacceptance_retest": first_retest,
                "price_reacceptance_excursions": price_excursions,
                "structural_retest_excursions": structural_retest_excursions,
                "full_retest_excursions": full_retest_excursions,
            },
        },
    )
    return result


def _median_metric(
    cases: list[dict[str, Any]],
    *,
    field: str,
    horizon: int,
    metric: str,
) -> float | None:
    values: list[float] = []
    for case in cases:
        item = (case["component_cascade"].get(field) or {}).get(str(horizon))
        if item is not None:
            values.append(float(item[metric]))
    return median(values) if values else None


def aggregate(cases: list[dict[str, Any]], profitable: bool) -> dict[str, Any]:
    result = _BASE_AGGREGATE(cases, profitable)
    selected = [
        case for case in cases if case["original_v38_profitable"] is profitable
    ]
    cascade_names = (
        "price_reaccepted",
        "directional_body",
        "flow_15s_aligned",
        "flow_60s_aligned",
        "efficient",
        "active",
        "threatened_depth_withdrawn",
        "close_location_confirmed",
    )
    cumulative_totals = {name: 0 for name in cascade_names}
    individual_totals = {name: 0 for name in cascade_names}
    for case in selected:
        cascade = case["component_cascade"]
        for name in cascade_names:
            cumulative_totals[name] += int(cascade["cumulative_bar_counts"][name])
            individual_totals[name] += int(cascade["individual_bar_counts"][name])

    structural = [
        case
        for case in selected
        if (
            (case["component_cascade"].get("first_price_reacceptance_retest") or {})
            .get("close_defended")
        )
    ]
    full = [
        case
        for case in selected
        if (
            (case["component_cascade"].get("first_price_reacceptance_retest") or {})
            .get("fully_defended")
        )
    ]
    cascade_horizons: dict[str, Any] = {}
    for horizon in HORIZONS:
        cascade_horizons[str(horizon)] = {
            "structural_retest_observations": sum(
                (case["component_cascade"].get("structural_retest_excursions") or {}).get(
                    str(horizon),
                )
                is not None
                for case in selected
            ),
            "structural_retest_median_mfe_atr": _median_metric(
                selected,
                field="structural_retest_excursions",
                horizon=horizon,
                metric="maximum_favorable_excursion_atr",
            ),
            "structural_retest_median_mae_atr": _median_metric(
                selected,
                field="structural_retest_excursions",
                horizon=horizon,
                metric="maximum_adverse_excursion_atr",
            ),
            "full_retest_observations": sum(
                (case["component_cascade"].get("full_retest_excursions") or {}).get(
                    str(horizon),
                )
                is not None
                for case in selected
            ),
            "full_retest_median_mfe_atr": _median_metric(
                selected,
                field="full_retest_excursions",
                horizon=horizon,
                metric="maximum_favorable_excursion_atr",
            ),
            "full_retest_median_mae_atr": _median_metric(
                selected,
                field="full_retest_excursions",
                horizon=horizon,
                metric="maximum_adverse_excursion_atr",
            ),
        }
    result["component_cascade"] = {
        "cases_with_price_reacceptance": sum(
            bool(case["component_cascade"]["first_price_reacceptance"])
            for case in selected
        ),
        "price_reacceptances_during_original_position": sum(
            bool(
                case["component_cascade"][
                    "first_price_reacceptance_during_original_position"
                ],
            )
            for case in selected
        ),
        "first_retest_close_defended": len(structural),
        "first_retest_fully_defended": len(full),
        "individual_bar_counts": individual_totals,
        "cumulative_bar_counts": cumulative_totals,
        "median_maximum_original_direction_extension_beyond_sweep_atr": (
            median(
                float(
                    case["component_cascade"][
                        "maximum_original_direction_extension_beyond_sweep_atr"
                    ],
                )
                for case in selected
                if case["component_cascade"][
                    "maximum_original_direction_extension_beyond_sweep_atr"
                ]
                is not None
            )
            if selected
            else None
        ),
        "horizons": cascade_horizons,
    }
    return result


def write_json(path, value: Any) -> None:
    if isinstance(value, dict) and value.get("schema") == (
        "candidate-05-v38-failure-path-diagnostic-v2"
    ):
        value = {
            **value,
            "schema": "candidate-05-v38-failure-path-component-cascade-v1",
            "component_cascade_purpose": (
                "DISTINGUISH_ABSENT_PRICE_REACCEPTANCE_FROM_MISSING_CAUSAL_SUPPORT"
            ),
        }
    _BASE_WRITE_JSON(path, value)


_v2.analyze_case = analyze_case
_v2.aggregate = aggregate
_v2.write_json = write_json
_base.analyze_case = analyze_case
_base.aggregate = aggregate
_base.write_json = write_json


def main() -> None:
    _base.main()


if __name__ == "__main__":
    main()
