"""Counterfactual answer-sheet construction for causal EasyChart plans.

The strategy already records every completed plan before global-slot arbitration.
This module uses the checksum-verified one-minute tape which was available to the
backtest and labels each plan only after research execution has ended:

* first target touch, first stop touch, same-minute ambiguity, or unresolved;
* maximum favorable/adverse excursion in R and fixed horizons;
* conservative post-cost R under the project's immutable full-position contract;
* causal local/common-market return and aggressor-flow state at plan time.

Future bars are used only to create research labels.  None of the labels are
available to or imported by the trading strategy.  Same-minute stop/target cases
are explicitly ambiguous and receive the stop outcome in the conservative field.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from data_re1_flow import load_range_flow
from fee_profiles_v5 import FEE_PROFILES
from instruments import CONTRACTS


LABEL_POLICY = (
    "RESEARCH_ONLY:FUTURE_ONE_MINUTE_FIRST_PASSAGE_LABELS_NEVER_IMPORTED_BY_STRATEGY;"
    "SAME_MINUTE_STOP_TARGET_IS_AMBIGUOUS_AND_CONSERVATIVELY_STOP_FIRST"
)
MARKET_STATE_POLICY = (
    "CAUSAL_FEATURES:LOCAL_LOG_RETURN_AND_TAKER_DELTA_NORMALIZED_ONLY_BY_PRIOR_DATA;"
    "COMMON_FACTOR_IS_CROSS_SYMBOL_MEDIAN_AT_THE_SAME_COMPLETED_MINUTE"
)


@dataclass(frozen=True, slots=True)
class HarvestConfig:
    start: date
    end: date
    load_start: date
    symbols: tuple[str, ...]
    cache: Path
    output: Path
    fee_profile: str
    entry_slippage_ticks: int
    stop_slippage_ticks: int


def _safe_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(float(value))


def _side_sign(side: str) -> float:
    if side == "LONG":
        return 1.0
    if side == "SHORT":
        return -1.0
    raise ValueError(f"unknown side {side!r}")


def _rolling_median_scale(values: pd.Series, window: int, minimum: int) -> pd.Series:
    scale = values.abs().rolling(window, min_periods=minimum).median()
    positive = scale[scale > 0.0]
    fallback = float(positive.median()) if not positive.empty else 1e-12
    return scale.where(scale > 0.0, fallback).fillna(fallback).clip(lower=1e-12)


def _symbol_state(symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy().sort_values("open_time_dt")
    data["ts"] = pd.DatetimeIndex(data["open_time_dt"]) + pd.Timedelta(minutes=1)
    data = data.set_index("ts", drop=True)
    log_close = np.log(data["close"].astype(float).clip(lower=1e-12))
    ret1 = log_close.diff()
    ret_scale = _rolling_median_scale(ret1, 1440, 120)
    signed_quote = 2.0 * data["taker_buy_quote_volume"].astype(float) - data["quote_volume"].astype(float)
    quote = data["quote_volume"].astype(float).clip(lower=0.0)

    output = pd.DataFrame(index=data.index)
    output["symbol"] = symbol
    for minutes in (5, 15, 60):
        raw_return = log_close.diff(minutes)
        horizon_scale = ret_scale * math.sqrt(minutes)
        output[f"local_return_{minutes}m"] = raw_return
        output[f"local_return_z_{minutes}m"] = raw_return / horizon_scale
        q = quote.rolling(minutes, min_periods=minutes).sum()
        d = signed_quote.rolling(minutes, min_periods=minutes).sum()
        output[f"local_delta_share_{minutes}m"] = d / q.replace(0.0, np.nan)
        output[f"local_quote_{minutes}m"] = q
        output[f"local_signed_quote_{minutes}m"] = d

    quote_baseline = quote.rolling(1440, min_periods=120).median().clip(lower=1e-12)
    trade_baseline = data["count"].astype(float).rolling(1440, min_periods=120).median().clip(lower=1.0)
    output["local_activity_ratio_1m"] = quote / quote_baseline
    output["local_trade_count_ratio_1m"] = data["count"].astype(float) / trade_baseline
    output["local_delta_share_1m"] = signed_quote / quote.replace(0.0, np.nan)
    output["local_close_location_1m"] = (
        (data["close"] - data["low"]) / (data["high"] - data["low"]).replace(0.0, np.nan)
    )
    output["local_range_fraction_1m"] = (
        (data["high"] - data["low"]) / data["open"].replace(0.0, np.nan)
    )
    output["local_body_fraction_1m"] = (
        (data["close"] - data["open"]) / (data["high"] - data["low"]).replace(0.0, np.nan)
    )
    return output.replace([np.inf, -np.inf], np.nan)


def _market_state(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    local = pd.concat([_symbol_state(symbol, frame) for symbol, frame in frames.items()])
    local = local.reset_index().rename(columns={"index": "ts"})
    factor_columns = [
        column
        for column in local.columns
        if column.startswith("local_return_z_") or column.startswith("local_delta_share_")
    ]
    common = local.groupby("ts", sort=True)[factor_columns].median().add_prefix("common_")
    output = local.join(common, on="ts")
    for column in factor_columns:
        output[f"residual_{column.removeprefix('local_')}"] = output[column] - output[f"common_{column}"]
    return output.set_index(["symbol", "ts"]).sort_index()


def _best_trace_rows(events: pd.DataFrame) -> pd.DataFrame:
    traces = events[(events["kind"] == "scenario_transition") & events["plan_id"].notna()].copy()
    if traces.empty:
        return pd.DataFrame(index=pd.Index([], name="plan_id"))
    traces["_nonnull"] = traces.notna().sum(axis=1)
    traces["_time"] = pd.to_numeric(traces["event_time_ns"], errors="coerce").fillna(
        pd.to_numeric(traces["ts_ns"], errors="coerce"),
    )
    selected = traces.sort_values(["plan_id", "_nonnull", "_time"]).groupby("plan_id", sort=False).tail(1)
    keep = [
        "plan_id",
        "scenario_kind",
        "flow_kind",
        "flow_mechanism",
        "flow_strength",
        "flow_quote_volume",
        "flow_median_quote_volume",
        "flow_activity_ratio",
        "flow_trade_count",
        "flow_trade_size_ratio",
        "flow_taker_buy_quote_volume",
        "flow_signed_taker_quote",
        "flow_delta_share",
        "flow_delta_ratio",
        "flow_body_ratio",
        "flow_range_ratio",
        "flow_close_location",
        "flow_impact_per_activity",
        "flow_episode_bars",
        "flow_episode_cumulative_delta",
        "flow_episode_net_price_progress",
        "state_before_flow",
        "acceptance",
    ]
    keep = [column for column in keep if column in selected.columns]
    return selected[keep].set_index("plan_id")


def _first_true_time(mask: pd.Series) -> pd.Timestamp | None:
    hit = mask[mask.fillna(False)]
    return None if hit.empty else pd.Timestamp(hit.index[0])


def _excursions(
    future: pd.DataFrame,
    side_sign: float,
    entry: float,
    risk: float,
) -> tuple[float, float]:
    if future.empty:
        return 0.0, 0.0
    if side_sign > 0:
        favorable = (future["high"].max() - entry) / risk
        adverse = (future["low"].min() - entry) / risk
    else:
        favorable = (entry - future["low"].min()) / risk
        adverse = (entry - future["high"].max()) / risk
    return float(favorable), float(adverse)


def _estimated_net_r(
    *,
    side_sign: float,
    entry: float,
    exit_price: float,
    risk: float,
    tick: float,
    entry_slippage_ticks: int,
    exit_slippage_ticks: int,
    fee_rate: float,
) -> float:
    actual_entry = entry + side_sign * entry_slippage_ticks * tick
    actual_exit = exit_price - side_sign * exit_slippage_ticks * tick
    gross = side_sign * (actual_exit - actual_entry) / risk
    fee = fee_rate * (abs(actual_entry) + abs(actual_exit)) / risk
    return float(gross - fee)


def _label_plan(
    plan: pd.Series,
    tape: pd.DataFrame,
    state: pd.DataFrame,
    config: HarvestConfig,
) -> dict[str, Any]:
    symbol = str(plan["symbol"])
    side = str(plan["side"])
    sign = _side_sign(side)
    entry = float(plan["entry"])
    stop = float(plan["stop"])
    target = float(plan["target"])
    risk = abs(entry - stop)
    if risk <= 0.0:
        raise RuntimeError(f"nonpositive risk for {plan['plan_id']}")
    plan_ns = _safe_int(plan["ts_ns"])
    if plan_ns is None:
        raise RuntimeError(f"missing plan time for {plan['plan_id']}")
    plan_time = pd.Timestamp(plan_ns, unit="ns", tz="UTC")
    future = tape[tape.index > plan_time]

    if sign > 0:
        stop_mask = future["low"] <= stop
        target_mask = future["high"] >= target
    else:
        stop_mask = future["high"] >= stop
        target_mask = future["low"] <= target
    stop_time = _first_true_time(stop_mask)
    target_time = _first_true_time(target_mask)

    if stop_time is not None and target_time is not None and stop_time == target_time:
        outcome = "AMBIGUOUS_SAME_MINUTE"
        resolution = stop_time
        conservative_exit = stop
        conservative_exit_slippage = config.stop_slippage_ticks
    elif target_time is not None and (stop_time is None or target_time < stop_time):
        outcome = "TARGET_FIRST"
        resolution = target_time
        conservative_exit = target
        conservative_exit_slippage = 1
    elif stop_time is not None:
        outcome = "STOP_FIRST"
        resolution = stop_time
        conservative_exit = stop
        conservative_exit_slippage = config.stop_slippage_ticks
    else:
        outcome = "UNRESOLVED"
        resolution = None
        conservative_exit = float(future["close"].iloc[-1]) if not future.empty else entry
        conservative_exit_slippage = config.stop_slippage_ticks

    analysis_window = future if resolution is None else future[future.index <= resolution]
    mfe_r, mae_r = _excursions(analysis_window, sign, entry, risk)
    record: dict[str, Any] = {
        "counterfactual_outcome": outcome,
        "counterfactual_resolution_time": None if resolution is None else resolution.isoformat(),
        "counterfactual_minutes_to_resolution": (
            None if resolution is None else (resolution.value - plan_ns) / 60_000_000_000
        ),
        "counterfactual_stop_time": None if stop_time is None else stop_time.isoformat(),
        "counterfactual_target_time": None if target_time is None else target_time.isoformat(),
        "counterfactual_mfe_r": mfe_r,
        "counterfactual_mae_r": mae_r,
        "counterfactual_net_r_conservative": _estimated_net_r(
            side_sign=sign,
            entry=entry,
            exit_price=conservative_exit,
            risk=risk,
            tick=float(CONTRACTS[symbol].price_increment),
            entry_slippage_ticks=config.entry_slippage_ticks,
            exit_slippage_ticks=conservative_exit_slippage,
            fee_rate=float(FEE_PROFILES[config.fee_profile].taker_rate),
        ),
    }
    for minutes in (15, 30, 60, 240, 720, 1440):
        horizon = future[future.index <= plan_time + pd.Timedelta(minutes=minutes)]
        h_mfe, h_mae = _excursions(horizon, sign, entry, risk)
        record[f"mfe_r_{minutes}m"] = h_mfe
        record[f"mae_r_{minutes}m"] = h_mae

    state_key = (symbol, plan_time)
    if state_key in state.index:
        for key, value in state.loc[state_key].items():
            record[key] = value
    return record


def harvest_counterfactual_plans(config: HarvestConfig) -> dict[str, Any]:
    events_path = config.output / "decision_events.csv"
    if not events_path.exists():
        raise FileNotFoundError(events_path)
    events = pd.read_csv(events_path, low_memory=False)
    plans = events[events["kind"] == "plan"].copy()
    if plans.empty:
        empty = config.output / "counterfactual_plans.csv"
        plans.to_csv(empty, index=False)
        summary = {"plans": 0, "label_policy": LABEL_POLICY, "market_state_policy": MARKET_STATE_POLICY}
        (config.output / "counterfactual_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary

    traces = _best_trace_rows(events)
    plans = plans.set_index("plan_id", drop=False).join(traces.add_prefix("trace_"), how="left")
    frames = {
        symbol: load_range_flow(symbol, config.load_start, config.end, config.cache)
        for symbol in config.symbols
    }
    tapes: dict[str, pd.DataFrame] = {}
    for symbol, raw in frames.items():
        tape = raw.copy()
        tape.index = pd.DatetimeIndex(tape.pop("open_time_dt")) + pd.Timedelta(minutes=1)
        tapes[symbol] = tape.sort_index()
    state = _market_state(frames)

    labels = []
    for _, plan in plans.sort_values(["ts_ns", "symbol", "plan_id"]).iterrows():
        labels.append(_label_plan(plan, tapes[str(plan["symbol"])], state, config))
    labelled = pd.concat(
        [plans.reset_index(drop=True), pd.DataFrame(labels, index=plans.reset_index(drop=True).index)],
        axis=1,
    )
    labelled.to_csv(config.output / "counterfactual_plans.csv", index=False)

    summary: dict[str, Any] = {
        "plans": int(len(labelled)),
        "target_first": int((labelled["counterfactual_outcome"] == "TARGET_FIRST").sum()),
        "stop_first": int((labelled["counterfactual_outcome"] == "STOP_FIRST").sum()),
        "ambiguous_same_minute": int((labelled["counterfactual_outcome"] == "AMBIGUOUS_SAME_MINUTE").sum()),
        "unresolved": int((labelled["counterfactual_outcome"] == "UNRESOLVED").sum()),
        "sum_conservative_net_r": float(labelled["counterfactual_net_r_conservative"].sum()),
        "mean_conservative_net_r": float(labelled["counterfactual_net_r_conservative"].mean()),
        "label_policy": LABEL_POLICY,
        "market_state_policy": MARKET_STATE_POLICY,
        "by_family": {},
    }
    for family, group in labelled.groupby("family", dropna=False):
        key = "<NA>" if pd.isna(family) else str(family)
        summary["by_family"][key] = {
            "plans": int(len(group)),
            "target_first": int((group["counterfactual_outcome"] == "TARGET_FIRST").sum()),
            "sum_conservative_net_r": float(group["counterfactual_net_r_conservative"].sum()),
            "mean_conservative_net_r": float(group["counterfactual_net_r_conservative"].mean()),
        }
    (config.output / "counterfactual_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
