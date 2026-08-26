"""Derivatives-classified auction event followed by structural first-return entry.

This candidate fuses two mechanisms that were individually incomplete:

* price-only sweep/break events produced many false episodes because they did not
  distinguish forced futures flow from genuine price discovery;
* derivatives dislocation entries were too early because they entered on the
  first reclaim/reacceleration rather than waiting for a fresh displacement origin
  and its first controlled return.

The combined sequence is:
pre-existing 15m/60m liquidity -> derivatives-classified failed/accepted auction
-> local BOS displacement leaving a fresh FVG/OB origin -> first return -> completed
price/flow response -> next-minute entry -> event invalidation -> nearest pre-existing
opposing objective.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence
import json
import math

import numpy as np
import pandas as pd

from auction_episode_research import CONTRACTS, PivotLevel, _resample_flow, _stable_id, detect_pivots, label_action
from derivatives_dislocation import prepare_market_state
from liquidity_displacement import (
    _action_from_sequence,
    _add_common_state,
    _displacement_zone,
    _event_candidate,
    _first_return_response,
)
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics

POLICY = (
    "AUCTION_DISLOCATION_CONFLUENCE:PREEXISTING_LIQUIDITY_THEN_DERIVATIVES_CLASSIFIED_"
    "FAILED_OR_ACCEPTED_AUCTION_THEN_BOS_DISPLACEMENT_FRESH_ORIGIN_FIRST_RETURN_"
    "COMPLETED_RESPONSE_NEXT_MINUTE_ENTRY"
)
MAX_EVENT_TO_DISPLACEMENT = 15
MAX_HOLD_MINUTES = 360
EPS = 1e-12


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _outward_direction(boundary: PivotLevel) -> float:
    return 1.0 if boundary.side == "HIGH" else -1.0


def _derivatives_context(data: pd.DataFrame, event: dict[str, Any], boundary: PivotLevel) -> dict[str, Any] | None:
    index = int(event["event_index"])
    row = data.iloc[index]
    outward = _outward_direction(boundary)
    action_sign = 1.0 if event["side"] == "LONG" else -1.0
    futures_return = _finite(row.get("futures_return_3m"), 0.0)
    index_return = _finite(row.get("index_return_3m"), 0.0)
    mark_return = _finite(row.get("mark_return_3m"), 0.0)
    basis_change = _finite(row.get("basis_change_3m_bps"), 0.0)
    mark_basis_change = _finite(row.get("mark_basis_change_3m_bps"), 0.0)
    oi_1 = _finite(row.get("metric_oi_log_change_1"), 0.0)
    oi_3 = _finite(row.get("metric_oi_log_change_3"), oi_1)
    basis_scale = max(_finite(row.get("past_basis_q90"), 0.0), EPS)
    shock_scale = max(_finite(row.get("past_shock_q98"), 0.0), EPS)
    pre_index = max(0, index - 15)
    pre_basis = _finite(data.iloc[pre_index:index]["basis_bps"].median(), _finite(row.get("past_basis_median"), 0.0))
    event_basis = _finite(row.get("basis_bps"), pre_basis)
    outward_futures = outward * futures_return
    outward_index = outward * index_return
    outward_mark = outward * mark_return
    outward_basis = outward * basis_change
    outward_mark_basis = outward * mark_basis_change
    lead_gap = outward_futures - outward_index
    features = {
        "base_auction_type": event["event_type"],
        "outward_futures_return_3m": outward_futures,
        "outward_index_return_3m": outward_index,
        "outward_mark_return_3m": outward_mark,
        "outward_basis_change_3m_bps": outward_basis,
        "outward_mark_basis_change_3m_bps": outward_mark_basis,
        "futures_index_lead_gap_3m": lead_gap,
        "shock_ratio_to_past_q98": abs(futures_return) / shock_scale,
        "basis_impulse_ratio_to_past_q90": abs(basis_change) / basis_scale,
        "oi_log_change_available_1": oi_1,
        "oi_log_change_available_3": oi_3,
        "event_basis_bps": event_basis,
        "pre_event_basis_bps": pre_basis,
        "event_action_delta_share_signed": action_sign * _finite(row.get("delta_share"), 0.0),
        "event_activity_ratio": _finite(row.get("activity_ratio"), 0.0),
        "event_impact_per_activity": _finite(row.get("impact_per_activity"), 0.0),
    }
    if event["event_type"] == "FAILED_AUCTION":
        if not (outward_futures > 0.0 and outward_basis > 0.0 and lead_gap > 0.0 and min(oi_1, oi_3) < 0.0):
            return None
        features["derivatives_mechanism"] = "FORCED_FUTURES_FLUSH"
    else:
        confirmation = outward_index / max(abs(outward_futures), EPS)
        if not (outward_futures > 0.0 and outward_index > 0.0 and confirmation >= 0.50 and max(oi_1, oi_3) > 0.0):
            return None
        if abs(outward_basis) > max(abs(outward_futures) * 10_000.0, basis_scale * 2.0):
            return None
        features["derivatives_mechanism"] = "INDEX_CONFIRMED_FRESH_POSITIONING"
        features["index_confirmation_ratio"] = confirmation
    return features


def _response_derivatives_valid(data: pd.DataFrame, response: dict[str, Any], context: dict[str, Any], side: str) -> tuple[bool, dict[str, float]]:
    index = int(response["response_index"])
    row = data.iloc[index]
    sign = 1.0 if side == "LONG" else -1.0
    pre_basis = float(context["pre_event_basis_bps"])
    event_basis = float(context["event_basis_bps"])
    current_basis = _finite(row.get("basis_bps"), pre_basis)
    denominator = max(abs(event_basis - pre_basis), EPS)
    repair = 1.0 - abs(current_basis - pre_basis) / denominator
    index_response = sign * _finite(row.get("index_return_5m"), 0.0)
    futures_response = sign * _finite(row.get("futures_return_5m"), 0.0)
    mark_response = sign * _finite(row.get("mark_return_5m"), 0.0)
    output = {
        "response_basis_repair_fraction": repair,
        "response_index_return_5m_signed": index_response,
        "response_futures_return_5m_signed": futures_response,
        "response_mark_return_5m_signed": mark_response,
        "response_basis_bps_signed": sign * current_basis,
    }
    if context["derivatives_mechanism"] == "FORCED_FUTURES_FLUSH":
        return repair >= 0.35 and futures_response > 0.0, output
    return index_response > 0.0 and mark_response > 0.0, output


def generate_symbol(symbol: str, data: pd.DataFrame, levels: Sequence[PivotLevel], trading_start: date):
    tick = CONTRACTS[symbol].tick_size
    source_levels = [level for level in levels if level.timeframe_minutes in (15, 60)]
    source_levels.sort(key=lambda level: (level.observed_index_1m, level.level_id))
    available: list[PivotLevel] = []
    next_source = 0
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    records: list[dict[str, Any]] = []
    classified_events = 0
    for index, (timestamp, row) in enumerate(data.iterrows()):
        while next_source < len(source_levels) and source_levels[next_source].observed_index_1m < index:
            available.append(source_levels[next_source])
            next_source += 1
        if int(timestamp.value) < start_ns or not available:
            continue
        touched = [level for level in available if not level.retired_as_source and float(row["low"]) <= level.upper and float(row["high"]) >= level.lower]
        if not touched:
            continue
        for source_side in ("LOW", "HIGH"):
            candidates = [level for level in touched if level.side == source_side]
            if not candidates:
                continue
            boundary = max(candidates, key=lambda level: (level.timeframe_minutes, level.defense_count, level.strength_ratio, -abs(level.price - float(row["close"]))))
            width = max(boundary.upper - boundary.lower, 4.0 * tick)
            for level in available:
                if level.side == source_side and not level.retired_as_source and abs(level.price - boundary.price) <= 1.5 * width:
                    level.retired_as_source = True
            event = _event_candidate(data, index, boundary, tick)
            if event is None:
                continue
            context = _derivatives_context(data, event, boundary)
            if context is None:
                continue
            classified_events += 1
            displacement = None
            for displacement_index in range(int(event["event_index"]) + 1, min(len(data), int(event["event_index"]) + MAX_EVENT_TO_DISPLACEMENT + 1)):
                displacement = _displacement_zone(data, displacement_index, str(event["side"]), tick)
                if displacement is not None:
                    break
            if displacement is None:
                continue
            response = _first_return_response(data, displacement, str(event["side"]), tick)
            if response is None:
                continue
            valid, response_derivatives = _response_derivatives_valid(data, response, context, str(event["side"]))
            if not valid:
                continue
            action = _action_from_sequence(symbol=symbol, data=data, levels=levels, boundary=boundary, interaction_index=index, event=event, zone=displacement, response=response, tick=tick)
            if action is None:
                continue
            if (action.side == "LONG" and action.stop >= action.entry) or (action.side == "SHORT" and action.stop <= action.entry):
                continue
            mechanism = str(context["derivatives_mechanism"])
            feature_values = {**action.feature_values, **context, **response_derivatives}
            event_ns = int(data.index[int(event["event_index"])].value)
            action = replace(
                action,
                action_id=f"ADC:{symbol}:{event_ns}:{mechanism}:{_stable_id(boundary.level_id)}",
                episode_id=f"ADC:{symbol}:{int(timestamp.value)}:{_stable_id(boundary.level_id)}",
                event_type=mechanism,
                decision_stage="DERIVATIVES_CLASSIFIED_DISPLACEMENT_FIRST_RETURN_RESPONSE",
                feature_values=feature_values,
            )
            label = label_action(data, action, tick)
            if label.holding_minutes is not None and label.holding_minutes > MAX_HOLD_MINUTES:
                continue
            records.append({**{key: value for key, value in asdict(action).items() if key != "feature_values"}, **action.feature_values, **asdict(label)})
    frame = pd.DataFrame(records)
    if not frame.empty and (frame.action_id.duplicated().any() or frame.episode_id.duplicated().any()):
        raise RuntimeError(f"duplicate confluence identity {symbol}")
    return frame, {"symbol": symbol, "classified_events": classified_events, "actions": len(frame), "outcomes": frame.outcome.value_counts().to_dict() if not frame.empty else {}}


def run_research(*, start: date, end: date, warmup_days: int, symbols: Sequence[str], cache: Path, output: Path):
    from data_re1_flow import load_range_flow

    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    load_start = start - timedelta(days=warmup_days)
    prepared: dict[str, pd.DataFrame] = {}
    levels_by_symbol: dict[str, list[PivotLevel]] = {}
    for symbol in symbols:
        tick = CONTRACTS[symbol].tick_size
        futures = load_range_flow(symbol, load_start, end, cache)
        index_price = load_reference_range("indexPriceKlines", symbol, load_start, end, cache)
        mark_price = load_reference_range("markPriceKlines", symbol, load_start, end, cache)
        metrics = load_range_metrics(symbol, load_start, end, cache)
        prepared[symbol] = prepare_market_state(futures, index_price, mark_price, metrics, tick)
        aggregates = {minutes: _resample_flow(futures, minutes) for minutes in (5, 15, 60)}
        levels_by_symbol[symbol] = detect_pivots(symbol, prepared[symbol], aggregates, tick)
    prepared = _add_common_state(prepared)
    frames: list[pd.DataFrame] = []
    by_symbol: dict[str, Any] = {}
    for symbol in symbols:
        frame, summary = generate_symbol(symbol, prepared[symbol], levels_by_symbol[symbol], start)
        by_symbol[symbol] = summary
        if not frame.empty:
            frame.to_csv(output / f"{symbol}_confluence_actions.csv", index=False)
            frames.append(frame)
    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    combined.to_csv(output / "confluence_actions.csv", index=False)
    resolved = combined[combined.outcome.isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_FILL_TARGET_SAME_MINUTE"])] if not combined.empty else combined
    summary = {
        "start": start.isoformat(), "end": end.isoformat(), "symbols": list(symbols),
        "actions": len(combined), "resolved": len(resolved),
        "wins": int((resolved.outcome == "TARGET_FIRST").sum()) if not resolved.empty else 0,
        "win_rate": float((resolved.outcome == "TARGET_FIRST").mean()) if not resolved.empty else None,
        "by_symbol": by_symbol, "policy": POLICY,
        "future_information_in_features": False, "future_information_in_labels_only": True,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
