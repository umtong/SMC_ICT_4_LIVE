"""Causal futures/index/mark dislocation around pre-existing liquidity.

Two different mechanisms are represented, not mixed into one pattern:

* FORCED_FLUSH_REVERSAL: a futures-led move sweeps a known 15m/60m boundary,
  open interest contracts, contract/index and mark/index dislocations expand,
  then the dislocation repairs and price reclaims local control.
* SPOT_CONFIRMED_INITIATIVE: contract and index move together through a known
  boundary while open interest expands; the first pullback holds and initiative
  resumes in the break direction.

All state is completed and available before emission. Future bars are used only
for conservative first-passage labels.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence
import json
import math

import numpy as np
import pandas as pd

from auction_episode_research import (
    CONTRACTS,
    ActionSpec,
    PivotLevel,
    _economics,
    _objective_candidates,
    _resample_flow,
    _stable_id,
    _time_ns,
    detect_pivots,
    label_action,
    prepare_one_minute,
)
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics, metric_features


POLICY = (
    "DERIVATIVES_DISLOCATION:PREEXISTING_15M_60M_LIQUIDITY_THEN_EITHER_"
    "OI_CONTRACTION_FUTURES_LED_SWEEP_DISLOCATION_REPAIR_RECLAIM_OR_"
    "OI_EXPANSION_INDEX_CONFIRMED_BREAK_FIRST_PULLBACK_REACCELERATION"
)
EPS = 1e-12
MAX_CONFIRM_MINUTES = 15
MAX_HOLD_MINUTES = 360


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _side_sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _prepare_reference(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    out = frame.copy()
    out.index = pd.DatetimeIndex(out.pop("open_time_dt")) + pd.Timedelta(minutes=1)
    out = out.sort_index()
    return out[["open", "high", "low", "close"]].rename(columns=lambda c: f"{prefix}_{c}")


def prepare_market_state(
    futures: pd.DataFrame,
    index_price: pd.DataFrame,
    mark_price: pd.DataFrame,
    metrics: pd.DataFrame,
    tick_size: float,
) -> pd.DataFrame:
    data = prepare_one_minute(futures, tick_size)
    data = data.join(_prepare_reference(index_price, "index"), how="inner")
    data = data.join(_prepare_reference(mark_price, "mark"), how="inner")
    data = pd.merge_asof(
        data.sort_index(),
        metric_features(metrics).sort_index(),
        left_index=True,
        right_index=True,
        direction="backward",
        allow_exact_matches=True,
    )
    log_futures = np.log(data["close"].clip(lower=tick_size))
    log_index = np.log(data["index_close"].clip(lower=tick_size))
    log_mark = np.log(data["mark_close"].clip(lower=tick_size))
    data["basis_bps"] = (log_futures - log_index) * 10_000.0
    data["mark_basis_bps"] = (log_mark - log_index) * 10_000.0
    data["contract_mark_bps"] = (log_futures - log_mark) * 10_000.0
    for minutes in (1, 3, 5, 15, 30):
        data[f"futures_return_{minutes}m"] = log_futures.diff(minutes)
        data[f"index_return_{minutes}m"] = log_index.diff(minutes)
        data[f"mark_return_{minutes}m"] = log_mark.diff(minutes)
        data[f"basis_change_{minutes}m_bps"] = data["basis_bps"].diff(minutes)
        data[f"mark_basis_change_{minutes}m_bps"] = data["mark_basis_bps"].diff(minutes)
    absolute_shock = data["futures_return_3m"].abs()
    absolute_basis = data["basis_change_3m_bps"].abs()
    data["past_shock_q98"] = absolute_shock.shift(1).rolling(1440, min_periods=720).quantile(0.98)
    data["past_basis_q90"] = absolute_basis.shift(1).rolling(1440, min_periods=720).quantile(0.90)
    data["past_basis_median"] = data["basis_bps"].shift(1).rolling(1440, min_periods=720).median()
    data["past_basis_mad"] = (
        (data["basis_bps"].shift(1) - data["past_basis_median"]).abs()
        .rolling(1440, min_periods=720).median()
    )
    return data.replace([np.inf, -np.inf], np.nan)


def _event_features(data: pd.DataFrame, index: int, direction: float) -> dict[str, float]:
    row = data.iloc[index]
    futures_ret = _finite(row.get("futures_return_3m"), 0.0)
    index_ret = _finite(row.get("index_return_3m"), 0.0)
    mark_ret = _finite(row.get("mark_return_3m"), 0.0)
    scale = max(_finite(row.get("past_shock_q98"), 0.0), EPS)
    basis_scale = max(_finite(row.get("past_basis_q90"), 0.0), EPS)
    return {
        "event_direction": direction,
        "futures_return_3m_signed": direction * futures_ret,
        "index_return_3m_signed": direction * index_ret,
        "mark_return_3m_signed": direction * mark_ret,
        "shock_ratio_to_past_q98": abs(futures_ret) / scale,
        "index_confirmation_ratio": direction * index_ret / max(abs(futures_ret), EPS),
        "mark_confirmation_ratio": direction * mark_ret / max(abs(futures_ret), EPS),
        "basis_change_3m_signed_bps": direction * _finite(row.get("basis_change_3m_bps"), 0.0),
        "mark_basis_change_3m_signed_bps": direction * _finite(row.get("mark_basis_change_3m_bps"), 0.0),
        "basis_impulse_ratio": abs(_finite(row.get("basis_change_3m_bps"), 0.0)) / basis_scale,
        "basis_robust_z": (
            (_finite(row.get("basis_bps"), 0.0) - _finite(row.get("past_basis_median"), 0.0))
            / max(1.4826 * _finite(row.get("past_basis_mad"), 0.0), EPS)
        ),
        "oi_change_available": _finite(row.get("metric_oi_log_change_1"), 0.0),
        "taker_position_change": _finite(row.get("metric_taker_change_1"), 0.0),
        "top_position_change": _finite(row.get("metric_top_position_change_1"), 0.0),
        "all_account_change": _finite(row.get("metric_all_account_change_1"), 0.0),
        "activity_ratio": _finite(row.get("activity_ratio"), 0.0),
        "delta_share_signed": direction * _finite(row.get("delta_share"), 0.0),
        "impact_per_activity": _finite(row.get("impact_per_activity"), 0.0),
    }


def _classify_event(data: pd.DataFrame, index: int, boundary: PivotLevel) -> tuple[str, float, dict[str, float]] | None:
    row = data.iloc[index]
    shock = _finite(row.get("futures_return_3m"), 0.0)
    threshold = _finite(row.get("past_shock_q98"), float("nan"))
    if not math.isfinite(threshold) or threshold <= 0.0 or abs(shock) < threshold:
        return None
    direction = 1.0 if shock > 0.0 else -1.0
    if boundary.side == "HIGH" and direction < 0.0:
        return None
    if boundary.side == "LOW" and direction > 0.0:
        return None
    crossed = float(row["high"]) > boundary.upper if direction > 0.0 else float(row["low"]) < boundary.lower
    if not crossed:
        return None
    features = _event_features(data, index, direction)
    oi_change = features["oi_change_available"]
    basis_expands = features["basis_change_3m_signed_bps"] > 0.0 and features["basis_impulse_ratio"] >= 1.0
    index_confirmed = features["index_confirmation_ratio"] >= 0.55
    if oi_change < 0.0 and basis_expands:
        return "FORCED_FLUSH_REVERSAL", direction, features
    if oi_change > 0.0 and index_confirmed and features["basis_impulse_ratio"] <= 1.5:
        return "SPOT_CONFIRMED_INITIATIVE", direction, features
    return None


def _confirmation(
    data: pd.DataFrame,
    event_index: int,
    boundary: PivotLevel,
    mechanism: str,
    direction: float,
) -> dict[str, Any] | None:
    pre_basis = _finite(data.iloc[max(0, event_index - 3)].get("basis_bps"), 0.0)
    event_basis = _finite(data.iloc[event_index].get("basis_bps"), pre_basis)
    cascade_extreme = float(data.iloc[event_index]["high"] if direction > 0.0 else data.iloc[event_index]["low"])
    pullback_extreme = cascade_extreme
    touched_pullback = False
    for index in range(event_index + 1, min(len(data), event_index + MAX_CONFIRM_MINUTES + 1)):
        row = data.iloc[index]
        previous = data.iloc[index - 1]
        cascade_extreme = (
            max(cascade_extreme, float(row["high"]))
            if direction > 0.0
            else min(cascade_extreme, float(row["low"]))
        )
        if mechanism == "FORCED_FLUSH_REVERSAL":
            side = "SHORT" if direction > 0.0 else "LONG"
            repair_denominator = max(abs(event_basis - pre_basis), EPS)
            repair_fraction = 1.0 - abs(_finite(row.get("basis_bps"), pre_basis) - pre_basis) / repair_denominator
            inside = float(row["close"]) <= boundary.lower if side == "SHORT" else float(row["close"]) >= boundary.upper
            local_control = float(row["close"]) < float(previous["low"]) if side == "SHORT" else float(row["close"]) > float(previous["high"])
            signed_delta = _side_sign(side) * _finite(row.get("delta_share"), 0.0)
            price_progress = _side_sign(side) * float(row["close"] - row["open"])
            response = signed_delta > 0.0 or (signed_delta <= 0.0 and price_progress > 0.0)
            if repair_fraction >= 0.5 and inside and local_control and response:
                return {
                    "side": side,
                    "emission_index": index,
                    "stop_reference": cascade_extreme,
                    "micro_reference": float(data.iloc[max(0, event_index - 3)]["close"]),
                    "confirmation_minutes": float(index - event_index),
                    "basis_repair_fraction": repair_fraction,
                    "confirmation_delta_share_signed": signed_delta,
                    "confirmation_kind": "DISLOCATION_REPAIR_RECLAIM",
                }
        else:
            side = "LONG" if direction > 0.0 else "SHORT"
            outside = float(row["close"]) >= boundary.upper if side == "LONG" else float(row["close"]) <= boundary.lower
            overlaps = float(row["low"]) <= boundary.upper and float(row["high"]) >= boundary.lower
            if overlaps:
                touched_pullback = True
            if touched_pullback:
                pullback_extreme = (
                    min(pullback_extreme, float(row["low"]))
                    if side == "LONG"
                    else max(pullback_extreme, float(row["high"]))
                )
            local_control = float(row["close"]) > float(previous["high"]) if side == "LONG" else float(row["close"]) < float(previous["low"])
            signed_delta = _side_sign(side) * _finite(row.get("delta_share"), 0.0)
            index_still_confirms = _side_sign(side) * _finite(row.get("index_return_5m"), 0.0) > 0.0
            if touched_pullback and outside and local_control and signed_delta > 0.0 and index_still_confirms:
                return {
                    "side": side,
                    "emission_index": index,
                    "stop_reference": pullback_extreme,
                    "micro_reference": None,
                    "confirmation_minutes": float(index - event_index),
                    "basis_repair_fraction": 0.0,
                    "confirmation_delta_share_signed": signed_delta,
                    "confirmation_kind": "FIRST_PULLBACK_REACCELERATION",
                }
    return None


def _make_action(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[PivotLevel],
    boundary: PivotLevel,
    event_index: int,
    mechanism: str,
    direction: float,
    event_features: dict[str, float],
    confirmation: dict[str, Any],
    tick: float,
) -> ActionSpec | None:
    side = str(confirmation["side"])
    emission_index = int(confirmation["emission_index"])
    emission_ns = _time_ns(data.index, emission_index)
    entry = float(data.iloc[emission_index]["close"])
    buffer = max(2.0 * tick, 0.05 * _finite(data.iloc[emission_index].get("prior_range_1m"), tick))
    stop_reference = float(confirmation["stop_reference"])
    stop = stop_reference - buffer if side == "LONG" else stop_reference + buffer
    objectives = _objective_candidates(
        levels,
        side=side,
        entry=entry,
        emission_index=emission_index,
        emission_time_ns=emission_ns,
        micro_reference=confirmation.get("micro_reference"),
        tick_size=tick,
    )
    if not objectives:
        return None
    objective = objectives[0]
    economics = _economics(
        side=side,
        entry=entry,
        stop=stop,
        target=objective.price,
        tick_size=tick,
        entry_style="MARKET",
    )
    if not economics or economics["gross_rr"] < 1.0 or economics["target_net_r"] <= 0.0 or economics["stop_net_r"] >= 0.0:
        return None
    event_ns = _time_ns(data.index, event_index)
    episode_id = f"DD:{symbol}:{event_ns}:{mechanism}:{_stable_id(boundary.level_id)}"
    action_id = f"{episode_id}:{confirmation['confirmation_kind']}"
    row = data.iloc[emission_index]
    feature_values: dict[str, Any] = {
        **economics,
        **event_features,
        "mechanism": mechanism,
        "confirmation_kind": confirmation["confirmation_kind"],
        "source_side": boundary.side,
        "source_strength_ratio": boundary.strength_ratio,
        "source_defense_count": boundary.defense_count,
        "source_age_minutes": (emission_ns - boundary.observed_time_ns) / 60_000_000_000.0,
        "confirmation_minutes": confirmation["confirmation_minutes"],
        "basis_repair_fraction": confirmation["basis_repair_fraction"],
        "confirmation_delta_share_signed": confirmation["confirmation_delta_share_signed"],
        "decision_basis_bps_signed": _side_sign(side) * _finite(row.get("basis_bps"), 0.0),
        "decision_mark_basis_bps_signed": _side_sign(side) * _finite(row.get("mark_basis_bps"), 0.0),
        "decision_contract_mark_bps_signed": _side_sign(side) * _finite(row.get("contract_mark_bps"), 0.0),
        "decision_activity_ratio": _finite(row.get("activity_ratio"), 0.0),
        "decision_impact_per_activity": _finite(row.get("impact_per_activity"), 0.0),
    }
    for column in row.index:
        if str(column).startswith("metric_"):
            feature_values[str(column)] = _finite(row[column], 0.0)
    return ActionSpec(
        action_id=action_id,
        episode_id=episode_id,
        symbol=symbol,
        event_type=mechanism,
        decision_stage=str(confirmation["confirmation_kind"]),
        side=side,
        emission_index=emission_index,
        emission_time_ns=emission_ns,
        entry_style="MARKET",
        entry=entry,
        stop=stop,
        target=float(objective.price),
        entry_expiry_minutes=1,
        source_level_id=boundary.level_id,
        source_kind=boundary.source_kind,
        source_timeframe_minutes=boundary.timeframe_minutes,
        source_span=boundary.span,
        source_price=boundary.price,
        source_lower=boundary.lower,
        source_upper=boundary.upper,
        source_strength_ratio=boundary.strength_ratio,
        source_defense_count=boundary.defense_count,
        source_age_minutes=(emission_ns - boundary.observed_time_ns) / 60_000_000_000.0,
        objective_id=objective.objective_id,
        objective_kind=objective.kind,
        objective_timeframe_minutes=objective.timeframe_minutes,
        objective_strength_ratio=objective.strength_ratio,
        interaction_time_ns=event_ns,
        feature_values=feature_values,
    )


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[PivotLevel],
    trading_start: date,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    tick = CONTRACTS[symbol].tick_size
    source_levels = [level for level in levels if level.timeframe_minutes in (15, 60)]
    source_levels.sort(key=lambda level: (level.observed_index_1m, level.level_id))
    available: list[PivotLevel] = []
    next_source = 0
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    records: list[dict[str, Any]] = []
    events = 0
    cooldown_until = -1
    for index, (timestamp, row) in enumerate(data.iterrows()):
        while next_source < len(source_levels) and source_levels[next_source].observed_index_1m < index:
            available.append(source_levels[next_source])
            next_source += 1
        if index <= cooldown_until or int(timestamp.value) < start_ns or not available:
            continue
        touched = [
            level for level in available
            if not level.retired_as_source
            and float(row["low"]) <= level.upper
            and float(row["high"]) >= level.lower
        ]
        if not touched:
            continue
        direction = 1.0 if _finite(row.get("futures_return_3m"), 0.0) > 0.0 else -1.0
        wanted_side = "HIGH" if direction > 0.0 else "LOW"
        candidates = [level for level in touched if level.side == wanted_side]
        if not candidates:
            continue
        boundary = max(candidates, key=lambda level: (level.timeframe_minutes, level.defense_count, level.strength_ratio))
        classified = _classify_event(data, index, boundary)
        if classified is None:
            continue
        mechanism, direction, event_features = classified
        events += 1
        confirmation = _confirmation(data, index, boundary, mechanism, direction)
        cooldown_until = index + MAX_CONFIRM_MINUTES
        width = max(boundary.upper - boundary.lower, 4.0 * tick)
        for level in available:
            if level.side == boundary.side and not level.retired_as_source and abs(level.price - boundary.price) <= 1.5 * width:
                level.retired_as_source = True
        if confirmation is None:
            continue
        action = _make_action(
            symbol, data, levels, boundary, index, mechanism, direction,
            event_features, confirmation, tick,
        )
        if action is None:
            continue
        label = label_action(data, action, tick)
        if label.holding_minutes is not None and label.holding_minutes > MAX_HOLD_MINUTES:
            continue
        records.append({
            **{key: value for key, value in asdict(action).items() if key != "feature_values"},
            **action.feature_values,
            **asdict(label),
        })
    frame = pd.DataFrame(records)
    if not frame.empty and (frame.action_id.duplicated().any() or frame.episode_id.duplicated().any()):
        raise RuntimeError(f"duplicate dislocation identity for {symbol}")
    return frame, {
        "symbol": symbol,
        "events": events,
        "actions": len(frame),
        "outcomes": frame.outcome.value_counts().to_dict() if not frame.empty else {},
    }


def run_research(
    *,
    start: date,
    end: date,
    warmup_days: int,
    symbols: Sequence[str],
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    from data_re1_flow import load_range_flow

    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    load_start = start - timedelta(days=warmup_days)
    frames: list[pd.DataFrame] = []
    by_symbol: dict[str, Any] = {}
    for symbol in symbols:
        tick = CONTRACTS[symbol].tick_size
        futures = load_range_flow(symbol, load_start, end, cache)
        index_price = load_reference_range("indexPriceKlines", symbol, load_start, end, cache)
        mark_price = load_reference_range("markPriceKlines", symbol, load_start, end, cache)
        metrics = load_range_metrics(symbol, load_start, end, cache)
        data = prepare_market_state(futures, index_price, mark_price, metrics, tick)
        aggregates = {minutes: _resample_flow(futures, minutes) for minutes in (5, 15, 60)}
        levels = detect_pivots(symbol, data, aggregates, tick)
        frame, summary = generate_symbol(symbol, data, levels, start)
        by_symbol[symbol] = summary
        if not frame.empty:
            frame.to_csv(output / f"{symbol}_derivatives_dislocation_actions.csv", index=False)
            frames.append(frame)
    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    combined.to_csv(output / "derivatives_dislocation_actions.csv", index=False)
    resolved = combined[combined.outcome.isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"])] if not combined.empty else combined
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbols": list(symbols),
        "actions": len(combined),
        "resolved": len(resolved),
        "wins": int((resolved.outcome == "TARGET_FIRST").sum()) if not resolved.empty else 0,
        "win_rate": float((resolved.outcome == "TARGET_FIRST").mean()) if not resolved.empty else None,
        "by_symbol": by_symbol,
        "policy": POLICY,
        "future_information_in_features": False,
        "future_information_in_labels_only": True,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
