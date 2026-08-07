#!/usr/bin/env python3
"""Retrospectively explain v104 common-acceptance displacement scarcity.

This is a diagnostic only. It does not create trade intents, alter the frozen
first-week decision, simulate execution, or calculate portfolio performance.
"""
from __future__ import annotations

import argparse
import copy
from decimal import Decimal
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import v53_nt_backtest as runner
from v53_nt_core import CostConfig, load_feature_matrix, load_raw_one_minute
import v104_external_liquidity_core as v104


def safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [safe(item) for item in value]
    return str(value)


def candle_scan(
    x: pd.DataFrame,
    *,
    positions: range,
    boundary: float,
    direction: int,
    config: v104.ExternalLiquidityConfig,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for position in positions:
        if position < 2 or position >= len(x):
            continue
        row = x.iloc[position]
        record: dict[str, Any] = {
            "close_utc": pd.Timestamp(x.index[position]).isoformat(),
            "position": position,
        }
        fields = ("raw_open", "raw_high", "raw_low", "raw_close", "body", "body_threshold", "atr")
        finite = v104._finite(row, fields)
        record["finite"] = finite
        if not finite:
            record["qualifies"] = False
            record["failed"] = ["NONFINITE"]
            output.append(record)
            continue
        atr_value = float(row["atr"])
        body = float(row["body"])
        body_threshold = float(row["body_threshold"])
        directional_body = direction * (float(row["raw_close"]) - float(row["raw_open"]))
        body_floor = max(body_threshold, config.minimum_displacement_body_atr * atr_value)
        close_outside = (
            float(row["raw_close"]) > boundary
            if direction > 0
            else float(row["raw_close"]) < boundary
        )
        two_back = x.iloc[position - 2]
        if direction > 0:
            fvg_low, fvg_high = float(two_back["raw_high"]), float(row["raw_low"])
        else:
            fvg_low, fvg_high = float(row["raw_high"]), float(two_back["raw_low"])
        gap = fvg_high - fvg_low
        minimum_gap = config.minimum_fvg_atr * atr_value
        checks = {
            "atr_positive": atr_value > 0.0,
            "directional_body": directional_body > 0.0,
            "body_floor": body >= body_floor,
            "close_outside_boundary": close_outside,
            "fvg_gap": math.isfinite(gap) and gap >= minimum_gap,
        }
        record.update(
            {
                "atr": atr_value,
                "body": body,
                "body_atr": body / max(atr_value, 1e-12),
                "body_prior_quantile": body_threshold,
                "body_floor": body_floor,
                "directional_body": directional_body,
                "raw_open": float(row["raw_open"]),
                "raw_high": float(row["raw_high"]),
                "raw_low": float(row["raw_low"]),
                "raw_close": float(row["raw_close"]),
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_gap": gap,
                "fvg_gap_atr": gap / max(atr_value, 1e-12),
                "minimum_fvg": minimum_gap,
                "checks": checks,
                "failed": [name for name, passed in checks.items() if not passed],
                "qualifies": all(checks.values()),
            }
        )
        output.append(record)
    return output


def forward_path(
    x: pd.DataFrame,
    *,
    start_position: int,
    boundary: float,
    direction: int,
    atr_value: float,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for minutes in (6, 20, 60, 180):
        view = x.iloc[start_position + 1 : min(start_position + 1 + minutes, len(x))]
        if view.empty:
            continue
        favorable_extreme = float(view["raw_high"].max()) if direction > 0 else float(view["raw_low"].min())
        adverse_extreme = float(view["raw_low"].min()) if direction > 0 else float(view["raw_high"].max())
        final_close = float(view.iloc[-1]["raw_close"])
        output[str(minutes)] = {
            "favorable_from_boundary": direction * (favorable_extreme - boundary),
            "favorable_from_boundary_atr": direction * (favorable_extreme - boundary) / max(atr_value, 1e-12),
            "adverse_back_inside_boundary": max(-direction * (adverse_extreme - boundary), 0.0),
            "adverse_back_inside_boundary_atr": max(-direction * (adverse_extreme - boundary), 0.0) / max(atr_value, 1e-12),
            "final_close_from_boundary": direction * (final_close - boundary),
            "final_close_from_boundary_atr": direction * (final_close - boundary) / max(atr_value, 1e-12),
        }
    return output


def run_variant(
    *,
    label: str,
    config: v104.ExternalLiquidityConfig,
    costs: CostConfig,
    features: pd.DataFrame,
    raw_all: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> dict[str, Any]:
    state = v104.build_state(features, config)
    raw_view = v104._normalise_index(raw_all[["open", "high", "low", "close"]])
    atr = v104._true_range(raw_view).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median().shift(1)
    levels = v104.build_liquidity_registry(raw_view, atr=atr, config=config)
    traces: list[dict[str, Any]] = []
    original = v104._find_displacement

    def traced_find_displacement(*, x, start_position, boundary, direction, config):
        event_position = start_position - config.classification_minutes + 1
        previous_position = event_position - 1
        segment = x.iloc[event_position : start_position + 1]
        last = segment.iloc[-1]
        previous = x.iloc[previous_position]
        atr_value = float(x.iloc[event_position]["atr"])
        pre_basis = float(previous["perp_spot_log_basis"])
        final_basis = float(last["perp_spot_log_basis"])
        spot_boundary = boundary / math.exp(pre_basis)
        final_perp = float(last["raw_close"])
        final_spot = float(last["spot_close"])
        perp_fraction = max(direction * (final_perp / boundary - 1.0), 1e-12)
        spot_fraction = direction * (final_spot / spot_boundary - 1.0)
        nearby_levels = [
            {
                "family": level.family,
                "level_id": level.level_id,
                "price": level.price,
                "side": level.side,
                "eligibility_utc": pd.Timestamp(level.eligibility_ns, unit="ns", tz="UTC").isoformat(),
                "expiry_utc": pd.Timestamp(level.expiry_ns, unit="ns", tz="UTC").isoformat(),
            }
            for level in levels
            if level.side == ("HIGH" if direction > 0 else "LOW")
            and abs(level.price - boundary) <= config.level_merge_atr * atr_value + 1e-12
        ]
        inside_scan = candle_scan(
            x,
            positions=range(max(event_position, 2), start_position + 1),
            boundary=boundary,
            direction=direction,
            config=config,
        )
        after_scan = candle_scan(
            x,
            positions=range(start_position + 1, min(start_position + config.displacement_search_minutes, len(x) - 1) + 1),
            boundary=boundary,
            direction=direction,
            config=config,
        )
        result = original(
            x=x,
            start_position=start_position,
            boundary=boundary,
            direction=direction,
            config=config,
        )
        traces.append(
            {
                "label": label,
                "direction": "UP" if direction > 0 else "DOWN",
                "event_close_utc": pd.Timestamp(x.index[event_position]).isoformat(),
                "acceptance_close_utc": pd.Timestamp(x.index[start_position]).isoformat(),
                "boundary": boundary,
                "boundary_levels": nearby_levels,
                "event_atr": atr_value,
                "outside_close_count": int((segment["raw_close"] > boundary).sum() if direction > 0 else (segment["raw_close"] < boundary).sum()),
                "final_perp_outside_atr": direction * (final_perp - boundary) / max(atr_value, 1e-12),
                "final_spot_outside_atr": direction * (final_spot - spot_boundary) / max(atr_value, 1e-12),
                "spot_acceptance_ratio": spot_fraction / perp_fraction,
                "basis_expansion_share": max(direction * (final_basis - pre_basis), 0.0) / perp_fraction,
                "acceptance_segment_displacement_candidates": inside_scan,
                "post_acceptance_candidates": after_scan,
                "current_rule_found_displacement": result is not None,
                "current_rule_result": None if result is None else {
                    "position": result.position,
                    "close_utc": pd.Timestamp(x.index[result.position]).isoformat(),
                    "body_atr": result.body_atr,
                    "fvg_low": result.fvg_low,
                    "fvg_high": result.fvg_high,
                },
                "forward_path_from_acceptance": forward_path(
                    x,
                    start_position=start_position,
                    boundary=boundary,
                    direction=direction,
                    atr_value=atr_value,
                ),
            }
        )
        return result

    v104._find_displacement = traced_find_displacement
    try:
        result = v104.build_scenario_result(
            state=state,
            raw=raw_all,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            config=config,
            costs=costs,
        )
    finally:
        v104._find_displacement = original
    return {
        "label": label,
        "diagnostics": result.diagnostics,
        "level_counts": result.level_counts,
        "scheduled_signals": len(result.signals),
        "accepted_event_traces": traces,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base = json.loads(args.config.read_text(encoding="utf-8"))
    scenario = v104.ExternalLiquidityConfig.from_mapping(base["scenario"])
    costs = CostConfig.from_mapping(base["costs"])
    npz_path, columns_path, raw_directory = runner._resolve_input_paths(args.input_root)
    features = load_feature_matrix(npz_path, columns_path)
    raw_all = load_raw_one_minute(raw_directory)
    start = pd.Timestamp(base["validation"]["first_week_start"], tz="UTC")
    end = start + pd.Timedelta(days=7)

    without_equal_values = copy.deepcopy(base["scenario"])
    without_equal_values["level_families"] = [
        value for value in without_equal_values["level_families"] if value != "EQUAL_SWING_CLUSTER"
    ]
    variants = [
        run_variant(
            label="baseline",
            config=scenario,
            costs=costs,
            features=features,
            raw_all=raw_all,
            evaluation_start=start,
            evaluation_end=end,
        ),
        run_variant(
            label="without_equal_swing_clusters",
            config=v104.ExternalLiquidityConfig.from_mapping(without_equal_values),
            costs=costs,
            features=features,
            raw_all=raw_all,
            evaluation_start=start,
            evaluation_end=end,
        ),
    ]
    payload = {
        "classification": "RETROSPECTIVE_LOGIC_DIAGNOSTIC_NOT_PERFORMANCE_EVIDENCE",
        "candidate": base["candidate"],
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "variants": variants,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(safe(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(safe(payload), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
