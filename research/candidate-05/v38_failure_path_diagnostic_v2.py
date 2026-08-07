#!/usr/bin/env python3
"""Unambiguous schema for the observational v38 failure-path diagnostic.

The original diagnostic correctly stopped after the first failed retest, but it
used one field for two different observations: movement from the reacceptance
bar and movement from a defended retest. This compatibility layer preserves the
frozen market predicates and data contract while reporting those references
separately. It creates no orders, fills, positions, fees, account state or PnL.
"""
from __future__ import annotations

from statistics import median
from typing import Any

import pandas as pd

import v38_failure_path_diagnostic as _base


HORIZONS = _base.HORIZONS
_BASE_ANALYZE_CASE = _base.analyze_case
_BASE_WRITE_JSON = _base.write_json


def _selected_observation_frame(
    case: dict[str, Any],
    frame: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    confirmation_ts = int(case["confirmation_ts"])
    close_ts = int(case["position_close_ts"])
    observation_end = close_ts + int(config["max_hold_bars"]) * 60 * 1_000_000_000
    return frame[
        (frame["observed_time_ns"] > confirmation_ts)
        & (frame["observed_time_ns"] <= observation_end)
    ].copy().reset_index(drop=True)


def _event_reference(
    selected: pd.DataFrame,
    *,
    ts_event: int | None,
    continuation_side: int,
    sweep_extreme: float,
    sweep_atr: float,
) -> tuple[float | None, dict[str, Any] | None]:
    if ts_event is None:
        return None, None
    matches = selected.index[selected["observed_time_ns"] == int(ts_event)].tolist()
    if len(matches) != 1:
        raise RuntimeError(
            f"diagnostic event timestamp must map to one completed bar: {ts_event}",
        )
    index = int(matches[0])
    reference_price = float(selected.iloc[index]["close"])
    excursions = _base.excursion_metrics(
        selected,
        start_index=index,
        continuation_side=continuation_side,
        reference_price=reference_price,
        sweep_extreme=sweep_extreme,
        sweep_atr=sweep_atr,
    )
    return reference_price, excursions


def analyze_case(
    case: dict[str, Any],
    frame: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Return distinct reacceptance and defended-retest observation references."""
    result = _BASE_ANALYZE_CASE(case, frame, config)
    selected = _selected_observation_frame(case, frame, config)
    continuation_side = int(result["continuation_side"])
    sweep_extreme = float(case["sweep_extreme"])
    sweep_atr = float(case["sweep_atr"])

    reacceptance = result.get("reacceptance") or {}
    reacceptance_price, reacceptance_excursions = _event_reference(
        selected,
        ts_event=reacceptance.get("ts_event"),
        continuation_side=continuation_side,
        sweep_extreme=sweep_extreme,
        sweep_atr=sweep_atr,
    )

    first_touch = result.get("first_touch") or {}
    defended_retest_ts = (
        first_touch.get("ts_event")
        if result.get("first_touch_result") == "FIRST_RETEST_DEFENDED"
        else None
    )
    retest_price, retest_excursions = _event_reference(
        selected,
        ts_event=defended_retest_ts,
        continuation_side=continuation_side,
        sweep_extreme=sweep_extreme,
        sweep_atr=sweep_atr,
    )

    # Remove the v1 field whose meaning depended on whether a retest completed.
    result.pop("continuation_reference_price", None)
    result.pop("continuation_excursions", None)
    result.update(
        {
            "reacceptance_reference_price": reacceptance_price,
            "reacceptance_excursions": reacceptance_excursions,
            "retest_reference_price": retest_price,
            "retest_excursions": retest_excursions,
        },
    )
    return result


def aggregate(cases: list[dict[str, Any]], profitable: bool) -> dict[str, Any]:
    """Aggregate only defended-first-retest excursions as executable evidence."""
    selected = [
        case for case in cases if case["original_v38_profitable"] is profitable
    ]
    reaccepted = [case for case in selected if case["reacceptance"] is not None]
    during = [
        case for case in reaccepted if case["reacceptance_during_original_position"]
    ]
    retested = [
        case
        for case in selected
        if case["first_touch_result"] == "FIRST_RETEST_DEFENDED"
    ]
    aggregate_horizons: dict[str, Any] = {}
    for horizon in HORIZONS:
        favorable: list[float] = []
        adverse: list[float] = []
        for case in retested:
            item = (case.get("retest_excursions") or {}).get(str(horizon))
            if item is None:
                continue
            favorable.append(float(item["maximum_favorable_excursion_atr"]))
            adverse.append(float(item["maximum_adverse_excursion_atr"]))
        aggregate_horizons[str(horizon)] = {
            "observations": len(favorable),
            "median_maximum_favorable_excursion_atr": (
                median(favorable) if favorable else None
            ),
            "median_maximum_adverse_excursion_atr": (
                median(adverse) if adverse else None
            ),
            "at_least_one_atr_favorable": sum(value >= 1.0 for value in favorable),
            "at_least_two_atr_favorable": sum(value >= 2.0 for value in favorable),
        }
    return {
        "cases": len(selected),
        "reacceptances": len(reaccepted),
        "reacceptances_during_original_position": len(during),
        "defended_first_retests": len(retested),
        "first_touch_failures": sum(
            case["first_touch_result"] == "FIRST_RETEST_FAILED"
            for case in selected
        ),
        "reacceptance_failures_before_retest": sum(
            case["first_touch_result"] == "REACCEPTANCE_FAILED_BEFORE_RETEST"
            for case in selected
        ),
        "horizons": aggregate_horizons,
    }


def write_json(path, value: Any) -> None:
    if isinstance(value, dict) and value.get("schema") == (
        "candidate-05-v38-failure-path-diagnostic-v1"
    ):
        value = {
            **value,
            "schema": "candidate-05-v38-failure-path-diagnostic-v2",
            "schema_repair": (
                "REACCEPTANCE_AND_DEFENDED_RETEST_REFERENCES_REPORTED_SEPARATELY"
            ),
        }
    _BASE_WRITE_JSON(path, value)


# The v1 main function resolves these names from its module globals at runtime.
_base.analyze_case = analyze_case
_base.aggregate = aggregate
_base.write_json = write_json


def main() -> None:
    _base.main()


if __name__ == "__main__":
    main()
