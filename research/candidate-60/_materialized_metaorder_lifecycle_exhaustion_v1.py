#!/usr/bin/env python3
"""Causal development study of persistent-order-flow lifecycle exhaustion.

The study uses the exact Candidate-51 real-data feature contract and returned
one-minute klines.  It creates no NautilusTrader promotion claim.  It separates
state detection, next-open geometry, source-bracket path, one global causal
episode and one global slot before a fresh interval can be considered.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RESEARCH = Path(os.environ.get("C60_RESEARCH_ROOT", str(HERE.parent))).resolve()
C51 = RESEARCH / "candidate-51"
while str(C51) in sys.path:
    sys.path.remove(str(C51))
sys.path.insert(0, str(C51))

import run as c51run  # noqa: E402

SYMBOLS = tuple(c51run.SYMBOLS)
SYMBOL_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}
SCHEMA = "candidate-60-metaorder-lifecycle-exhaustion-v1"
FLOW_BASELINE_MINUTES = 240
FLOW_BASELINE_MINIMUM = 60
MIN_RUN_AGE = 7
MIN_CUMULATIVE_IMPACT_BPS = 20.0
ATR_PERIOD = 30
STOP_BUFFER_ATR = 0.15
ROUND_TRIP_COST_BPS = 20.0
MIN_COST_AWARE_RR = 1.0
MAX_HOLD_MINUTES = 60
GLOBAL_EPISODE_MINUTES = 3
RISK_FRACTION = 0.03
FIXED_HORIZONS = (1, 3, 5, 15, 30, 60, 120)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _describe(values: Iterable[float]) -> dict[str, Any]:
    data = np.asarray([float(value) for value in values if _finite(value)], dtype=float)
    if data.size == 0:
        return {"n": 0}
    ordered = np.sort(data)
    absolute = float(np.abs(data).sum())
    return {
        "n": int(data.size),
        "mean": float(data.mean()),
        "median": float(np.median(data)),
        "minimum": float(data.min()),
        "maximum": float(data.max()),
        "positive_rate": float((data > 0.0).mean()),
        "largest_absolute_share": (
            float(np.abs(data).max() / absolute) if absolute > 0.0 else 0.0
        ),
        "trim_best_mean": float(ordered[:-1].mean()) if data.size > 1 else None,
        "trim_worst_mean": float(ordered[1:].mean()) if data.size > 1 else None,
    }


def _atr(frame: pd.DataFrame, period: int) -> pd.Series:
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    previous = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous).abs(),
            (low - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period, min_periods=period).mean()


def _normalize_clock(frame: pd.DataFrame, features: pd.DataFrame, symbol: str) -> None:
    close_ns = (
        pd.to_datetime(frame["close_time_dt"], utc=True)
        .astype("datetime64[ns, UTC]")
        .astype("int64")
        .to_numpy()
    )
    original = (
        pd.to_numeric(features["observed_time_ns"], errors="raise")
        .astype("int64")
        .to_numpy()
    )
    scale_matches = [
        factor
        for factor in (1, 1_000, 1_000_000)
        if np.array_equal(original * factor, close_ns)
    ]
    if len(scale_matches) != 1:
        raise RuntimeError(f"ambiguous feature clock for {symbol}: {scale_matches}")
    features["observed_time_ns"] = close_ns


def _future_fixed_returns(
    frame: pd.DataFrame,
    event_index: int,
    entry_index: int,
    side: int,
) -> dict[str, float]:
    result: dict[str, float] = {}
    entry = float(frame.iloc[entry_index]["open"])
    for horizon in FIXED_HORIZONS:
        exit_index = entry_index + horizon - 1
        if exit_index >= len(frame):
            result[f"gross_{horizon}m_bps"] = math.nan
            result[f"net_{horizon}m_bps"] = math.nan
            result[f"opposite_net_{horizon}m_bps"] = math.nan
            continue
        exit_close = float(frame.iloc[exit_index]["close"])
        gross = side * math.log(exit_close / entry) * 10_000.0
        result[f"gross_{horizon}m_bps"] = gross
        result[f"net_{horizon}m_bps"] = gross - ROUND_TRIP_COST_BPS
        result[f"opposite_net_{horizon}m_bps"] = -gross - ROUND_TRIP_COST_BPS
    return result


def _geometry(
    *,
    entry: float,
    side: int,
    objective: float,
    stop: float,
) -> dict[str, Any]:
    if side > 0:
        ordered = 0.0 < stop < entry < objective
        reward = objective / entry - 1.0
        risk = 1.0 - stop / entry
    else:
        ordered = 0.0 < objective < entry < stop
        reward = 1.0 - objective / entry
        risk = stop / entry - 1.0
    reward_bps = reward * 10_000.0
    risk_bps = risk * 10_000.0
    net_reward_bps = reward_bps - ROUND_TRIP_COST_BPS
    planned_loss_bps = risk_bps + ROUND_TRIP_COST_BPS
    cost_rr = (
        net_reward_bps / planned_loss_bps
        if ordered and net_reward_bps > 0.0 and planned_loss_bps > 0.0
        else math.nan
    )
    eligible = bool(
        ordered
        and net_reward_bps > 0.0
        and _finite(cost_rr)
        and cost_rr >= MIN_COST_AWARE_RR
    )
    return {
        "geometry_ordered": bool(ordered),
        "gross_reward_bps": reward_bps,
        "gross_risk_bps": risk_bps,
        "net_reward_bps": net_reward_bps,
        "planned_loss_bps": planned_loss_bps,
        "cost_aware_reward_r": cost_rr,
        "geometry_eligible": eligible,
    }


def _simulate_bracket(
    frame: pd.DataFrame,
    entry_index: int,
    side: int,
    entry: float,
    target: float,
    stop: float,
) -> dict[str, Any]:
    last_index = min(len(frame) - 1, entry_index + MAX_HOLD_MINUTES - 1)
    exit_index = last_index
    exit_price = float(frame.iloc[last_index]["close"])
    reason = "TIMEOUT"
    target_hit = False
    stop_hit = False
    for index in range(entry_index, last_index + 1):
        row = frame.iloc[index]
        high = float(row["high"])
        low = float(row["low"])
        if side > 0:
            touches_stop = low <= stop
            touches_target = high >= target
        else:
            touches_stop = high >= stop
            touches_target = low <= target
        if touches_stop and touches_target:
            exit_index = index
            exit_price = stop
            reason = "SAME_BAR_STOP_FIRST"
            stop_hit = True
            break
        if touches_stop:
            exit_index = index
            exit_price = stop
            reason = "STOP"
            stop_hit = True
            break
        if touches_target:
            exit_index = index
            exit_price = target
            reason = "TARGET"
            target_hit = True
            break

    gross_bps = side * math.log(exit_price / entry) * 10_000.0
    net_bps = gross_bps - ROUND_TRIP_COST_BPS
    return {
        "exit_index": int(exit_index),
        "exit_time": pd.Timestamp(frame.iloc[exit_index]["close_time_dt"]).isoformat(),
        "exit_price": exit_price,
        "exit_reason": reason,
        "target_hit": target_hit,
        "stop_hit": stop_hit,
        "holding_minutes": int(exit_index - entry_index + 1),
        "bracket_gross_bps": gross_bps,
        "bracket_net_bps": net_bps,
    }


def _build_symbol_events(
    symbol: str,
    frame: pd.DataFrame,
    features: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    frame = frame.sort_values("close_time_dt", kind="stable").reset_index(drop=True)
    features = features.reset_index(drop=True).copy()
    if len(frame) != len(features):
        raise RuntimeError(
            f"feature/kline rows differ for {symbol}: {len(features)} != {len(frame)}"
        )
    _normalize_clock(frame, features, symbol)
    frame["atr"] = _atr(frame, ATR_PERIOD)
    abs_flow = pd.to_numeric(features["flow_3m"], errors="coerce").abs()
    features["flow_baseline"] = (
        abs_flow.rolling(
            FLOW_BASELINE_MINUTES,
            min_periods=FLOW_BASELINE_MINIMUM,
        )
        .median()
        .shift(1)
    )
    sign_60 = np.sign(
        pd.to_numeric(features["flow_60s"], errors="coerce").fillna(0.0)
    ).astype(int)
    sign_3m = np.sign(
        pd.to_numeric(features["flow_3m"], errors="coerce").fillna(0.0)
    ).astype(int)
    ready = features["feature_ready"]
    if ready.dtype != bool:
        ready = ready.astype(str).str.strip().str.lower().isin({"true", "1", "yes"})
    else:
        ready = ready.fillna(False)
    state_ready = (
        ready
        & (sign_60 == sign_3m)
        & (sign_3m != 0)
        & (abs_flow >= features["flow_baseline"])
        & (pd.to_numeric(features["notional_burst"], errors="coerce") >= 1.0)
        & (pd.to_numeric(features["trade_count_burst"], errors="coerce") >= 1.0)
    )
    direction = np.where(state_ready, sign_3m, 0).astype(int)

    counters: dict[str, int] = defaultdict(int)
    events: list[dict[str, Any]] = []
    current_direction = 0
    age = 0
    run_start_open = math.nan
    run_high = -math.inf
    run_low = math.inf
    cumulative_impact = 0.0
    mean_directional_return = 0.0
    mean_efficiency = 0.0
    emitted = False

    for index, run_direction in enumerate(direction):
        counters["scored_minutes"] += 1
        if run_direction == 0:
            current_direction = 0
            age = 0
            emitted = False
            continue
        if run_direction != current_direction:
            current_direction = int(run_direction)
            age = 0
            run_start_open = float(frame.iloc[index]["open"])
            run_high = -math.inf
            run_low = math.inf
            cumulative_impact = 0.0
            mean_directional_return = 0.0
            mean_efficiency = 0.0
            emitted = False
            counters["directional_runs"] += 1

        age += 1
        counters["active_run_minutes"] += 1
        bar = frame.iloc[index]
        feature = features.iloc[index]
        run_high = max(run_high, float(bar["high"]))
        run_low = min(run_low, float(bar["low"]))
        directional_return = current_direction * float(feature["ret_60s_bps"])
        efficiency = float(feature["efficiency_60s"])
        cumulative_impact += directional_return
        mean_directional_return += (
            directional_return - mean_directional_return
        ) / age
        mean_efficiency += (efficiency - mean_efficiency) / age

        if age >= MIN_RUN_AGE:
            counters["run_age_ready_minutes"] += 1
        if age >= MIN_RUN_AGE and cumulative_impact >= MIN_CUMULATIVE_IMPACT_BPS:
            counters["impact_ready_minutes"] += 1
        impact_decay = directional_return < mean_directional_return
        efficiency_decay = efficiency < mean_efficiency
        if (
            emitted
            or age < MIN_RUN_AGE
            or cumulative_impact < MIN_CUMULATIVE_IMPACT_BPS
            or not impact_decay
            or not efficiency_decay
        ):
            continue

        counters["exhaustion_candidates"] += 1
        emitted = True
        entry_index = index + 1
        if entry_index >= len(frame) or not _finite(frame.iloc[index]["atr"]):
            counters["history_or_next_open_unavailable"] += 1
            continue

        proposed_side = -current_direction
        entry = float(frame.iloc[entry_index]["open"])
        atr = float(frame.iloc[index]["atr"])
        target = run_start_open
        stop = (
            run_low - STOP_BUFFER_ATR * atr
            if proposed_side > 0
            else run_high + STOP_BUFFER_ATR * atr
        )
        geometry = _geometry(
            entry=entry,
            side=proposed_side,
            objective=target,
            stop=stop,
        )
        if geometry["geometry_ordered"]:
            counters["ordered_geometry"] += 1
        if geometry["net_reward_bps"] > 0.0:
            counters["objective_beyond_cost"] += 1
        if geometry["geometry_eligible"]:
            counters["geometry_eligible"] += 1

        event: dict[str, Any] = {
            "symbol": symbol,
            "symbol_priority": SYMBOL_PRIORITY[symbol],
            "event_index": int(index),
            "event_time": pd.Timestamp(bar["close_time_dt"]).isoformat(),
            "entry_index": int(entry_index),
            "entry_time": pd.Timestamp(
                frame.iloc[entry_index]["open_time_dt"]
            ).isoformat(),
            "run_direction": int(current_direction),
            "side": int(proposed_side),
            "run_age": int(age),
            "run_start_open": run_start_open,
            "run_high": run_high,
            "run_low": run_low,
            "cumulative_impact_bps": cumulative_impact,
            "directional_return_now_bps": directional_return,
            "running_mean_directional_return_bps": mean_directional_return,
            "efficiency_now": efficiency,
            "running_mean_efficiency": mean_efficiency,
            "impact_decay": impact_decay,
            "efficiency_decay": efficiency_decay,
            "flow_60s": float(feature["flow_60s"]),
            "flow_3m": float(feature["flow_3m"]),
            "notional_burst": float(feature["notional_burst"]),
            "trade_count_burst": float(feature["trade_count_burst"]),
            "opposite_depth_change": float(
                feature["ask_depth_change_1_1m"]
                if current_direction > 0
                else feature["bid_depth_change_1_1m"]
            ),
            "same_depth_change": float(
                feature["bid_depth_change_1_1m"]
                if current_direction > 0
                else feature["ask_depth_change_1_1m"]
            ),
            "premium_change_1m_directional": (
                current_direction * float(feature["premium_change_1m"])
                if _finite(feature["premium_change_1m"])
                else math.nan
            ),
            "oi_change_5m": (
                float(feature["oi_change_5m"])
                if _finite(feature["oi_change_5m"])
                else math.nan
            ),
            "entry_price": entry,
            "target": target,
            "stop": stop,
            "atr": atr,
            **geometry,
            **_future_fixed_returns(
                frame,
                event_index=index,
                entry_index=entry_index,
                side=proposed_side,
            ),
        }
        if geometry["geometry_ordered"]:
            event.update(
                _simulate_bracket(
                    frame,
                    entry_index,
                    proposed_side,
                    entry,
                    target,
                    stop,
                )
            )
            event["bracket_net_r"] = (
                event["bracket_net_bps"] / geometry["planned_loss_bps"]
                if geometry["planned_loss_bps"] > 0.0
                else math.nan
            )
        else:
            event.update(
                {
                    "exit_index": -1,
                    "exit_time": None,
                    "exit_price": math.nan,
                    "exit_reason": "GEOMETRY_INVALID",
                    "target_hit": False,
                    "stop_hit": False,
                    "holding_minutes": 0,
                    "bracket_gross_bps": math.nan,
                    "bracket_net_bps": math.nan,
                    "bracket_net_r": math.nan,
                }
            )
        events.append(event)

    return pd.DataFrame(events), dict(counters)


def _collapse_episodes(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    ordered = events.sort_values(
        ["entry_time", "cost_aware_reward_r", "symbol_priority"],
        ascending=[True, False, True],
        kind="stable",
    )
    gap = pd.Timedelta(minutes=GLOBAL_EPISODE_MINUTES)
    clusters: list[pd.DataFrame] = []
    current: list[pd.Series] = []
    last_time: pd.Timestamp | None = None
    for _, row in ordered.iterrows():
        timestamp = pd.Timestamp(row["entry_time"])
        if last_time is None or timestamp - last_time <= gap:
            current.append(row)
        else:
            clusters.append(pd.DataFrame(current))
            current = [row]
        last_time = timestamp
    if current:
        clusters.append(pd.DataFrame(current))
    selected = [
        cluster.sort_values(
            ["cost_aware_reward_r", "symbol_priority", "entry_time"],
            ascending=[False, True, True],
            kind="stable",
        ).iloc[0]
        for cluster in clusters
    ]
    return pd.DataFrame(selected).reset_index(drop=True)


def _one_slot(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    ordered = events.sort_values(
        ["entry_time", "cost_aware_reward_r", "symbol_priority"],
        ascending=[True, False, True],
        kind="stable",
    )
    selected: list[pd.Series] = []
    free_time: pd.Timestamp | None = None
    for _, row in ordered.iterrows():
        entry_time = pd.Timestamp(row["entry_time"])
        exit_time = pd.Timestamp(row["exit_time"])
        if free_time is not None and entry_time < free_time:
            continue
        selected.append(row)
        free_time = exit_time + pd.Timedelta(nanoseconds=1)
    return pd.DataFrame(selected).reset_index(drop=True)


def _account(events: pd.DataFrame, calendar_days: int) -> dict[str, Any]:
    nav = 1.0
    peak = 1.0
    drawdown = 0.0
    account_returns: list[float] = []
    for _, row in events.iterrows():
        account_return = RISK_FRACTION * float(row["bracket_net_r"])
        if account_return <= -1.0:
            raise RuntimeError("diagnostic account reached ruin")
        nav *= 1.0 + account_return
        peak = max(peak, nav)
        drawdown = max(drawdown, 1.0 - nav / peak)
        account_returns.append(account_return)
    return {
        "starting_nav": 1.0,
        "ending_nav": nav,
        "total_return": nav - 1.0,
        "geometric_daily_growth": (
            nav ** (1.0 / calendar_days) - 1.0 if calendar_days > 0 else math.nan
        ),
        "max_drawdown": drawdown,
        "completed_trades": int(len(events)),
        "account_returns": _describe(account_returns),
    }


def _summarize(events: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {"events": int(len(events))}
    if events.empty:
        return result
    result["symbols"] = int(events["symbol"].nunique())
    result["longs"] = int((events["side"] > 0).sum())
    result["shorts"] = int((events["side"] < 0).sum())
    result["by_symbol"] = {
        str(symbol): {
            "events": int(len(group)),
            "bracket_net_r": _describe(group["bracket_net_r"]),
            "net_60m_bps": _describe(group["net_60m_bps"]),
        }
        for symbol, group in events.groupby("symbol", sort=True)
    }
    result["cost_aware_reward_r"] = _describe(events["cost_aware_reward_r"])
    result["bracket_net_bps"] = _describe(events["bracket_net_bps"])
    result["bracket_net_r"] = _describe(events["bracket_net_r"])
    result["exit_reason_counts"] = {
        str(key): int(value)
        for key, value in events["exit_reason"].value_counts().sort_index().items()
    }
    for horizon in FIXED_HORIZONS:
        result[f"gross_{horizon}m_bps"] = _describe(
            events[f"gross_{horizon}m_bps"]
        )
        result[f"net_{horizon}m_bps"] = _describe(
            events[f"net_{horizon}m_bps"]
        )
        result[f"opposite_net_{horizon}m_bps"] = _describe(
            events[f"opposite_net_{horizon}m_bps"]
        )
    return result


def run(
    *,
    start: date,
    end: date,
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    klines, feature_paths, input_records = c51run.load_inputs(
        start=start,
        end=end,
        cache=cache,
        output=cache / "materialized-source",
    )
    frames: list[pd.DataFrame] = []
    funnels: dict[str, Any] = {}
    for symbol in SYMBOLS:
        features = pd.read_csv(feature_paths[symbol], compression="infer")
        events, counters = _build_symbol_events(symbol, klines[symbol], features)
        frames.append(events)
        funnels[symbol] = counters

    all_events = (
        pd.concat(frames, ignore_index=True)
        if any(not frame.empty for frame in frames)
        else pd.DataFrame()
    )
    eligible = (
        all_events[all_events["geometry_eligible"].astype(bool)].copy()
        if not all_events.empty
        else all_events.copy()
    )
    episodes = _collapse_episodes(eligible)
    one_slot = _one_slot(episodes)

    output.mkdir(parents=True, exist_ok=True)
    all_events.to_csv(output / "all_candidates.csv", index=False)
    eligible.to_csv(output / "geometry_eligible.csv", index=False)
    episodes.to_csv(output / "global_episodes_3m.csv", index=False)
    one_slot.to_csv(output / "one_slot_events.csv", index=False)

    calendar_days = (end - start).days + 1
    result = {
        "schema": SCHEMA,
        "role": (
            "consumed-development causal lifecycle/geometry screen; not a "
            "NautilusTrader fill/account or fresh-data promotion claim"
        ),
        "interval": {"start": start.isoformat(), "end": end.isoformat()},
        "calendar_days": calendar_days,
        "universe": list(SYMBOLS),
        "frozen_policy": {
            "flow_baseline_minutes": FLOW_BASELINE_MINUTES,
            "flow_baseline_minimum": FLOW_BASELINE_MINIMUM,
            "minimum_run_age": MIN_RUN_AGE,
            "minimum_cumulative_impact_bps": MIN_CUMULATIVE_IMPACT_BPS,
            "impact_decay": "current directional 1m return < running run mean",
            "efficiency_decay": "current efficiency < running run mean",
            "entry": "next minute open, fade run direction",
            "target": "run-start open",
            "stop": "run extreme plus 0.15 ATR30",
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
            "minimum_cost_aware_reward_r": MIN_COST_AWARE_RR,
            "maximum_hold_minutes": MAX_HOLD_MINUTES,
            "same_bar_policy": "stop first",
            "global_episode_minutes": GLOBAL_EPISODE_MINUTES,
            "risk_fraction": RISK_FRACTION,
        },
        "funnels": funnels,
        "all_candidates": _summarize(all_events),
        "geometry_eligible": _summarize(eligible),
        "global_episodes": _summarize(episodes),
        "one_slot": _summarize(one_slot),
        "diagnostic_continuous_account": _account(one_slot, calendar_days),
        "input_records": input_records,
        "interpretation_contract": {
            "positive_result_is_not_sufficient_for_fresh_authorization": True,
            "state_and_geometry_are_evaluated_separately": True,
            "exact_opposite_fixed_horizons_recorded": True,
            "reserved_fresh_interval": ["2026-08-03", "2026-08-09"],
            "reserved_fresh_consumed": False,
        },
    }
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        start=args.start,
        end=args.end,
        cache=args.cache.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
