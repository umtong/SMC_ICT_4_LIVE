#!/usr/bin/env python3
"""Observe price and liquidity after frozen v38 reversal attempts.

This module is deliberately not a backtest engine. It downloads and verifies the
same Binance public-data inputs through ``features.load_range`` and reports
completed-bar market states around already frozen v38 trade cases. It creates no
orders, fills, positions, fees, account state or PnL.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

from timestamp_contract import install as install_timestamp_contract

install_timestamp_contract()

from external_acceptance_retest_logic import accepted_level_invalidated
from failed_reversal_continuation_logic import continuation_reacceptance_ready
from failed_reversal_continuation_logic import first_continuation_retest_response
from features import load_range


HORIZONS = (15, 30, 60, 180)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def utc_date(ts_ns: int) -> date:
    return pd.to_datetime(int(ts_ns), unit="ns", utc=True).date()


def completed_bar_frame(klines: pd.DataFrame, feature_path: Path) -> pd.DataFrame:
    bars = klines[
        ["close_time_dt", "open", "high", "low", "close", "volume"]
    ].copy()
    bars["observed_time_ns"] = pd.Series(
        (pd.Timestamp(value).value for value in bars["close_time_dt"]),
        dtype="int64",
    )
    features = pd.read_csv(feature_path, compression="infer")
    features["observed_time_ns"] = pd.to_numeric(
        features["observed_time_ns"],
        errors="raise",
    ).astype("int64")
    frame = bars.merge(
        features,
        on="observed_time_ns",
        how="inner",
        validate="one_to_one",
    )
    frame = frame.sort_values("observed_time_ns").reset_index(drop=True)
    if frame.empty or frame["observed_time_ns"].duplicated().any():
        raise RuntimeError("diagnostic completed-bar frame is empty or duplicated")
    return frame


def finite_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def reacceptance_details(
    row: pd.Series,
    *,
    continuation_side: int,
    sweep_extreme: float,
    sweep_atr: float,
) -> dict[str, Any]:
    threatened = (
        finite_number(row["ask_depth_change_1_1m"])
        if continuation_side > 0
        else finite_number(row["bid_depth_change_1_1m"])
    )
    return {
        "ts_event": int(row["observed_time_ns"]),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "outside_distance_atr": (
            continuation_side * (float(row["close"]) - sweep_extreme) / sweep_atr
        ),
        "flow_15s": finite_number(row["flow_15s"]),
        "flow_60s": finite_number(row["flow_60s"]),
        "efficiency_60s": finite_number(row["efficiency_60s"]),
        "notional_burst": finite_number(row["notional_burst"]),
        "depth_imbalance": finite_number(row["depth_imbalance_1"]),
        "threatened_depth_change_1m": threatened,
    }


def excursion_metrics(
    frame: pd.DataFrame,
    *,
    start_index: int,
    continuation_side: int,
    reference_price: float,
    sweep_extreme: float,
    sweep_atr: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for horizon in HORIZONS:
        future = frame.iloc[start_index + 1 : start_index + 1 + horizon]
        if future.empty:
            result[str(horizon)] = None
            continue
        if continuation_side > 0:
            favorable_price = float(future["high"].max())
            adverse_price = float(future["low"].min())
        else:
            favorable_price = float(future["low"].min())
            adverse_price = float(future["high"].max())
        favorable_atr = continuation_side * (favorable_price - reference_price) / sweep_atr
        adverse_atr = -continuation_side * (adverse_price - reference_price) / sweep_atr
        extension_beyond_sweep_atr = (
            continuation_side * (favorable_price - sweep_extreme) / sweep_atr
        )
        final_close = float(future.iloc[-1]["close"])
        result[str(horizon)] = {
            "bars_observed": int(len(future)),
            "maximum_favorable_excursion_atr": favorable_atr,
            "maximum_adverse_excursion_atr": adverse_atr,
            "maximum_extension_beyond_sweep_atr": extension_beyond_sweep_atr,
            "final_close_displacement_atr": (
                continuation_side * (final_close - reference_price) / sweep_atr
            ),
        }
    return result


def analyze_case(
    case: dict[str, Any],
    frame: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    reversal_side = int(case["side"])
    continuation_side = -reversal_side
    confirmation_ts = int(case["confirmation_ts"])
    close_ts = int(case["position_close_ts"])
    sweep_extreme = float(case["sweep_extreme"])
    sweep_atr = float(case["sweep_atr"])
    if sweep_atr <= 0.0:
        raise RuntimeError(f"invalid frozen sweep ATR: {case['scenario_id']}")

    observation_end = close_ts + config["max_hold_bars"] * 60 * 1_000_000_000
    selected = frame[
        (frame["observed_time_ns"] > confirmation_ts)
        & (frame["observed_time_ns"] <= observation_end)
    ].copy()
    selected = selected.reset_index(drop=True)

    reacceptance_index: int | None = None
    reacceptance: dict[str, Any] | None = None
    reacceptance_during_original_position = False
    for index, row in selected.iterrows():
        ready = continuation_reacceptance_ready(
            continuation_side=continuation_side,
            sweep_extreme=sweep_extreme,
            open_price=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=sweep_atr,
            flow_15s=finite_number(row["flow_15s"]),
            flow_60s=finite_number(row["flow_60s"]),
            efficiency_60s=finite_number(row["efficiency_60s"]),
            notional_burst=finite_number(row["notional_burst"]),
            bid_depth_change_1m=finite_number(row["bid_depth_change_1_1m"]),
            ask_depth_change_1m=finite_number(row["ask_depth_change_1_1m"]),
            minimum_close_distance_atr=float(config["acceptance_close_atr"]),
            minimum_flow=float(config["acceptance_flow_min"]),
            minimum_efficiency=float(config["acceptance_efficiency_min"]),
            minimum_notional_burst=float(config["sweep_min_notional_burst"]),
            minimum_depth_withdrawal=float(
                config["acceptance_depth_withdrawal_min"],
            ),
            minimum_close_location=float(config["acceptance_close_location"]),
        )
        if ready:
            reacceptance_index = int(index)
            reacceptance = reacceptance_details(
                row,
                continuation_side=continuation_side,
                sweep_extreme=sweep_extreme,
                sweep_atr=sweep_atr,
            )
            reacceptance_during_original_position = (
                int(row["observed_time_ns"]) <= close_ts
            )
            break

    first_touch: dict[str, Any] | None = None
    first_touch_result = "NO_REACCEPTANCE"
    retest_index: int | None = None
    if reacceptance_index is not None:
        first_touch_result = "NO_TOUCH_BEFORE_EXISTING_RETEST_WINDOW_EXPIRED"
        expiry = min(
            len(selected) - 1,
            reacceptance_index + int(config["acceptance_retrace_bars"]),
        )
        for index in range(reacceptance_index + 1, expiry + 1):
            row = selected.iloc[index]
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            touched = low <= sweep_extreme <= high
            if touched:
                defended = first_continuation_retest_response(
                    continuation_side=continuation_side,
                    sweep_extreme=sweep_extreme,
                    high=high,
                    low=low,
                    close=close,
                    flow_15s=finite_number(row["flow_15s"]),
                    depth_imbalance=finite_number(row["depth_imbalance_1"]),
                    maximum_counterflow=float(config["acceptance_max_counterflow"]),
                )
                first_touch = {
                    "ts_event": int(row["observed_time_ns"]),
                    "high": high,
                    "low": low,
                    "close": close,
                    "flow_15s": finite_number(row["flow_15s"]),
                    "depth_imbalance": finite_number(row["depth_imbalance_1"]),
                    "defended": defended,
                }
                if defended:
                    first_touch_result = "FIRST_RETEST_DEFENDED"
                    retest_index = index
                else:
                    first_touch_result = "FIRST_RETEST_FAILED"
                break
            if accepted_level_invalidated(
                side=continuation_side,
                level=sweep_extreme,
                close=close,
            ):
                first_touch = {
                    "ts_event": int(row["observed_time_ns"]),
                    "close": close,
                    "defended": False,
                }
                first_touch_result = "REACCEPTANCE_FAILED_BEFORE_RETEST"
                break

    reference_index = retest_index if retest_index is not None else reacceptance_index
    reference_price = None
    excursions = None
    if reference_index is not None:
        reference_price = float(selected.iloc[reference_index]["close"])
        excursions = excursion_metrics(
            selected,
            start_index=reference_index,
            continuation_side=continuation_side,
            reference_price=reference_price,
            sweep_extreme=sweep_extreme,
            sweep_atr=sweep_atr,
        )

    return {
        **case,
        "original_v38_profitable": float(case["realized_pnl"]) >= 0.0,
        "continuation_side": continuation_side,
        "observation_bars": int(len(selected)),
        "reacceptance": reacceptance,
        "reacceptance_during_original_position": reacceptance_during_original_position,
        "first_touch_result": first_touch_result,
        "first_touch": first_touch,
        "continuation_reference_price": reference_price,
        "continuation_excursions": excursions,
    }


def aggregate(cases: list[dict[str, Any]], profitable: bool) -> dict[str, Any]:
    selected = [case for case in cases if case["original_v38_profitable"] is profitable]
    reaccepted = [case for case in selected if case["reacceptance"] is not None]
    during = [case for case in reaccepted if case["reacceptance_during_original_position"]]
    retested = [
        case for case in selected if case["first_touch_result"] == "FIRST_RETEST_DEFENDED"
    ]
    aggregate_horizons: dict[str, Any] = {}
    for horizon in HORIZONS:
        values = []
        adverse = []
        for case in retested:
            item = (case.get("continuation_excursions") or {}).get(str(horizon))
            if item is None:
                continue
            values.append(float(item["maximum_favorable_excursion_atr"]))
            adverse.append(float(item["maximum_adverse_excursion_atr"]))
        aggregate_horizons[str(horizon)] = {
            "observations": len(values),
            "median_maximum_favorable_excursion_atr": (
                median(values) if values else None
            ),
            "median_maximum_adverse_excursion_atr": (
                median(adverse) if adverse else None
            ),
            "at_least_one_atr_favorable": sum(value >= 1.0 for value in values),
            "at_least_two_atr_favorable": sum(value >= 2.0 for value in values),
        }
    return {
        "cases": len(selected),
        "reacceptances": len(reaccepted),
        "reacceptances_during_original_position": len(during),
        "defended_first_retests": len(retested),
        "first_touch_failures": sum(
            case["first_touch_result"] == "FIRST_RETEST_FAILED" for case in selected
        ),
        "reacceptance_failures_before_retest": sum(
            case["first_touch_result"] == "REACCEPTANCE_FAILED_BEFORE_RETEST"
            for case in selected
        ),
        "horizons": aggregate_horizons,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    config_payload = json.loads(args.config.read_text(encoding="utf-8"))
    config = {
        **config_payload,
        **config_payload.get("strategy", {}),
    }
    cases = list(payload["cases"])
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[(str(case["week"]), str(case["symbol"]))].append(case)

    frames: dict[tuple[str, str], pd.DataFrame] = {}
    data_manifests: dict[str, Any] = {}
    for (week, symbol), items in grouped.items():
        start = min(utc_date(int(item["sweep_ts"])) for item in items) - timedelta(days=1)
        end = max(utc_date(int(item["position_close_ts"])) for item in items) + timedelta(days=1)
        destination = args.output / "data" / week / symbol
        klines, feature_path, raw_files, evidence = load_range(
            symbol=symbol,
            start=start,
            end=end,
            cache=args.cache / week / symbol,
            output=destination,
        )
        frames[(week, symbol)] = completed_bar_frame(klines, Path(feature_path))
        data_manifests[f"{week}:{symbol}"] = {
            "start": str(start),
            "end": str(end),
            "bars": int(len(klines)),
            "feature_path": str(Path(feature_path)),
            "raw_files": [str(path) for path in raw_files],
            "raw_evidence": [
                {
                    "endpoint": item.endpoint,
                    "day": item.day,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in evidence
            ],
        }

    analyzed = [
        analyze_case(
            case,
            frames[(str(case["week"]), str(case["symbol"]))],
            config,
        )
        for case in cases
    ]
    result = {
        "schema": "candidate-05-v38-failure-path-diagnostic-v1",
        "source_trade_cases": {
            "schema": payload.get("schema"),
            "source_commit": payload.get("source_commit"),
            "source_workflow_run_id": payload.get("source_workflow_run_id"),
        },
        "method": {
            "engine": "OBSERVATIONAL_COMPLETED_BAR_DIAGNOSTIC_ONLY",
            "creates_orders_or_pnl": False,
            "reacceptance_level": "ORIGINAL_SWEEP_EXTREME",
            "reacceptance_thresholds": {
                "minimum_close_distance_atr": config["acceptance_close_atr"],
                "minimum_flow": config["acceptance_flow_min"],
                "minimum_efficiency": config["acceptance_efficiency_min"],
                "minimum_notional_burst": config["sweep_min_notional_burst"],
                "minimum_depth_withdrawal": config[
                    "acceptance_depth_withdrawal_min"
                ],
                "minimum_close_location": config["acceptance_close_location"],
            },
            "first_retest_window_bars": config["acceptance_retrace_bars"],
            "maximum_observation_bars": config["max_hold_bars"],
            "forward_horizons_bars": list(HORIZONS),
        },
        "losing_original_v38_cases": aggregate(analyzed, profitable=False),
        "nonnegative_original_v38_cases": aggregate(analyzed, profitable=True),
        "cases": analyzed,
        "data_manifests": data_manifests,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "v38_failure_path_diagnostic.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()
