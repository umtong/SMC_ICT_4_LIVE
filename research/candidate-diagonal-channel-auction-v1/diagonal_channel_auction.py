"""Causal diagonal trendline/channel auction research.

The human cases repeatedly use diagonal structure as *state*, not decoration:

* a descending resistance line is broken, held and retested while an OB/FVG zone
  supports the first pullback;
* a channel or trendline is swept and reclaimed, proving that the apparent break
  was a liquidity acquisition rather than a new auction;
* the opposite channel boundary or the nearest pre-existing liquidity is the
  objective; the break/retest wave is the invalidation.

This module reconstructs only trendlines that were drawable at the time. Wick pivots
are confirmed after a right-hand span, candidate lines are created from already
confirmed pivots, and interactions occur strictly later. Horizontal liquidity remains
an objective/route map, not the entry generator.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
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
    _economics,
    _resample_flow,
    _stable_id,
    _time_ns,
    label_action,
)
from derivatives_dislocation import prepare_market_state
from hierarchical_liquidity_bpr import (
    LiquidityLevel,
    _clock_features,
    _volume_profile_features,
    detect_hierarchical_liquidity,
)
from liquidity_displacement import _add_common_state
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics

POLICY = (
    "CAUSAL_DIAGONAL_AUCTION:CONFIRMED_WICK_PIVOTS_FORM_ASCENDING_SUPPORT_OR_"
    "DESCENDING_RESISTANCE_AND_PARALLEL_CHANNEL;LATER_INTERACTION_IS_EITHER_"
    "SWEEP_RECLAIM_OR_BODY_BREAK_HOLD_FIRST_RETEST;ENTRY_FOLLOWS_COMPLETED_"
    "LOCAL_CONTROL_RESPONSE"
)
PIVOT_SPANS: dict[int, tuple[int, ...]] = {5: (2, 4), 15: (2, 4), 60: (2,)}
MAX_LINE_AGE_BARS: dict[int, int] = {5: 240, 15: 240, 60: 240}
MAX_CONFIRM_BARS = 4
MAX_RETEST_MINUTES = 45
MAX_RESPONSE_BARS = 3
MAX_HOLD_MINUTES = 360
EPS = 1e-12


@dataclass(frozen=True, slots=True)
class Pivot:
    pivot_id: str
    side: str
    timeframe_minutes: int
    center_position: int
    center_time_ns: int
    observed_time_ns: int
    observed_index_1m: int
    price: float
    atr: float


@dataclass(frozen=True, slots=True)
class DiagonalLine:
    line_id: str
    side: str  # SUPPORT or RESISTANCE
    timeframe_minutes: int
    first: Pivot
    second: Pivot
    slope_per_minute: float
    observed_index_1m: int
    observed_time_ns: int
    atr: float
    tolerance: float
    pivot_inliers: int
    prior_violation_fraction: float
    channel_offset: float | None
    opposite_pivot_inliers: int

    def price_at_ns(self, timestamp_ns: int) -> float:
        elapsed_minutes = (timestamp_ns - self.first.center_time_ns) / 60_000_000_000.0
        return self.first.price + self.slope_per_minute * elapsed_minutes

    def price_at_index(self, index: pd.DatetimeIndex, position: int) -> float:
        return self.price_at_ns(int(index[position].value))

    def opposite_at_index(self, index: pd.DatetimeIndex, position: int) -> float | None:
        if self.channel_offset is None:
            return None
        base = self.price_at_index(index, position)
        return base + self.channel_offset if self.side == "SUPPORT" else base - self.channel_offset


@dataclass(frozen=True, slots=True)
class DiagonalEvent:
    mechanism: str
    side: str
    interaction_index: int
    break_index: int | None
    hold_index: int | None
    retest_index: int | None
    response_index: int
    event_extreme: float
    stop_reference: float
    projected_line: float
    penetration: float
    response_kind: str


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _detect_pivots(raw: pd.DataFrame, one_minute: pd.DataFrame, tick: float) -> tuple[list[Pivot], dict[int, pd.DataFrame]]:
    aggregates = {minutes: _resample_flow(raw, minutes) for minutes in PIVOT_SPANS}
    output: list[Pivot] = []
    one_index = one_minute.index
    for timeframe, spans in PIVOT_SPANS.items():
        bars = aggregates[timeframe]
        ranges = (bars.high - bars.low).astype(float)
        prior_atr = ranges.shift(1).rolling(20, min_periods=5).median()
        highs, lows = bars.high.to_numpy(float), bars.low.to_numpy(float)
        for span in spans:
            for center in range(span, len(bars) - span):
                observed_position = center + span
                observed_time = bars.index[observed_position]
                observed_1m = int(one_index.searchsorted(observed_time, side="left"))
                if observed_1m >= len(one_index):
                    continue
                atr = max(_finite(prior_atr.iloc[observed_position], ranges.iloc[center]), tick)
                high_window = highs[center - span:center + span + 1]
                low_window = lows[center - span:center + span + 1]
                if highs[center] == np.nanmax(high_window) and int(np.sum(high_window == highs[center])) == 1:
                    output.append(Pivot(
                        pivot_id=f"{timeframe}m:H:c{center}:s{span}:{int(observed_time.value)}",
                        side="HIGH", timeframe_minutes=timeframe, center_position=center,
                        center_time_ns=int(bars.index[center].value), observed_time_ns=int(observed_time.value),
                        observed_index_1m=observed_1m, price=float(highs[center]), atr=atr,
                    ))
                if lows[center] == np.nanmin(low_window) and int(np.sum(low_window == lows[center])) == 1:
                    output.append(Pivot(
                        pivot_id=f"{timeframe}m:L:c{center}:s{span}:{int(observed_time.value)}",
                        side="LOW", timeframe_minutes=timeframe, center_position=center,
                        center_time_ns=int(bars.index[center].value), observed_time_ns=int(observed_time.value),
                        observed_index_1m=observed_1m, price=float(lows[center]), atr=atr,
                    ))
    output.sort(key=lambda item: (item.observed_time_ns, item.timeframe_minutes, item.side, item.pivot_id))
    return output, aggregates


def _line_value(first: Pivot, slope_per_minute: float, timestamp_ns: int) -> float:
    return first.price + slope_per_minute * (timestamp_ns - first.center_time_ns) / 60_000_000_000.0


def _construct_lines(
    pivots: Sequence[Pivot],
    one_minute: pd.DataFrame,
    aggregates: dict[int, pd.DataFrame],
    tick: float,
) -> list[DiagonalLine]:
    output: list[DiagonalLine] = []
    by_group: dict[tuple[int, str], list[Pivot]] = {}
    for pivot in pivots:
        by_group.setdefault((pivot.timeframe_minutes, pivot.side), []).append(pivot)
    for (timeframe, pivot_side), group in by_group.items():
        group.sort(key=lambda item: item.center_position)
        bars = aggregates[timeframe]
        opposite = [p for p in pivots if p.timeframe_minutes == timeframe and p.side != pivot_side]
        for second_position in range(1, len(group)):
            second = group[second_position]
            # Human-drawn lines usually use a recent meaningful pair, but a skipped
            # intermediate pivot can be the cleaner anchor. Test at most four prior
            # anchors without making multiple actions from the same line later.
            for first in group[max(0, second_position - 4):second_position]:
                spacing = second.center_position - first.center_position
                if spacing < 3 or spacing > 96:
                    continue
                rise = second.price - first.price
                valid_direction = rise > 0.0 if pivot_side == "LOW" else rise < 0.0
                if not valid_direction:
                    continue
                elapsed_minutes = (second.center_time_ns - first.center_time_ns) / 60_000_000_000.0
                if elapsed_minutes <= 0:
                    continue
                slope = rise / elapsed_minutes
                atr = max(first.atr, second.atr, tick)
                normalized_slope_per_bar = abs(rise) / atr / spacing
                if not (0.01 <= normalized_slope_per_bar <= 1.5):
                    continue
                tolerance = max(3.0 * tick, 0.10 * atr)
                known_end = second.observed_index_1m
                known_start = int(one_minute.index.searchsorted(pd.Timestamp(first.center_time_ns, unit="ns", tz="UTC"), side="left"))
                if known_end <= known_start:
                    continue
                known = one_minute.iloc[known_start:known_end + 1]
                projected = np.array([
                    _line_value(first, slope, int(ts.value)) for ts in known.index
                ])
                closes = known.close.to_numpy(float)
                violations = closes < projected - tolerance if pivot_side == "LOW" else closes > projected + tolerance
                violation_fraction = float(np.mean(violations)) if len(violations) else 1.0
                if violation_fraction > 0.08:
                    continue
                same_candidates = [p for p in group if first.center_position <= p.center_position <= second.center_position and p.observed_time_ns <= second.observed_time_ns]
                inliers = sum(abs(p.price - _line_value(first, slope, p.center_time_ns)) <= tolerance for p in same_candidates)
                if inliers < 2:
                    continue
                between = bars.iloc[first.center_position:second.center_position + 1]
                if between.empty:
                    continue
                if pivot_side == "LOW":
                    distances = [
                        float(row.high) - _line_value(first, slope, int(timestamp.value))
                        for timestamp, row in between.iterrows()
                    ]
                else:
                    distances = [
                        _line_value(first, slope, int(timestamp.value)) - float(row.low)
                        for timestamp, row in between.iterrows()
                    ]
                offset = max(distances) if distances else 0.0
                channel_offset = offset if offset >= 0.5 * atr else None
                opposite_inliers = 0
                if channel_offset is not None:
                    for pivot in opposite:
                        if first.center_position <= pivot.center_position <= second.center_position and pivot.observed_time_ns <= second.observed_time_ns:
                            base = _line_value(first, slope, pivot.center_time_ns)
                            channel = base + channel_offset if pivot_side == "LOW" else base - channel_offset
                            opposite_inliers += int(abs(pivot.price - channel) <= max(tolerance, 0.12 * channel_offset))
                    if opposite_inliers == 0:
                        channel_offset = None
                side = "SUPPORT" if pivot_side == "LOW" else "RESISTANCE"
                line_id = f"{timeframe}m:{side}:{_stable_id(first.pivot_id,second.pivot_id)}"
                output.append(DiagonalLine(
                    line_id=line_id, side=side, timeframe_minutes=timeframe,
                    first=first, second=second, slope_per_minute=slope,
                    observed_index_1m=known_end, observed_time_ns=second.observed_time_ns,
                    atr=atr, tolerance=tolerance, pivot_inliers=inliers,
                    prior_violation_fraction=violation_fraction,
                    channel_offset=channel_offset, opposite_pivot_inliers=opposite_inliers,
                ))
    # Prefer the wider-spaced, better-defended representation when near-identical
    # pairs project to the same line at observation.
    output.sort(key=lambda line: (line.observed_time_ns, line.timeframe_minutes, line.side, line.line_id))
    deduplicated: list[DiagonalLine] = []
    for line in output:
        projected = line.price_at_ns(line.observed_time_ns)
        duplicate = next((prior for prior in reversed(deduplicated[-40:]) if prior.side == line.side and prior.timeframe_minutes == line.timeframe_minutes and abs(prior.observed_time_ns - line.observed_time_ns) <= line.timeframe_minutes * 60_000_000_000 and abs(prior.price_at_ns(line.observed_time_ns) - projected) <= max(prior.tolerance, line.tolerance)), None)
        if duplicate is None:
            deduplicated.append(line)
        else:
            old_score = duplicate.pivot_inliers + 0.5 * duplicate.opposite_pivot_inliers - 5.0 * duplicate.prior_violation_fraction
            new_score = line.pivot_inliers + 0.5 * line.opposite_pivot_inliers - 5.0 * line.prior_violation_fraction
            if new_score > old_score:
                deduplicated[deduplicated.index(duplicate)] = line
    return deduplicated


def _response_at(data: pd.DataFrame, start: int, side: str, line: DiagonalLine, event_extreme: float, mechanism: str, break_index: int | None = None, hold_index: int | None = None, retest_index: int | None = None) -> DiagonalEvent | None:
    extreme = event_extreme
    for index in range(start, min(len(data), start + MAX_RESPONSE_BARS + 1)):
        row = data.iloc[index]
        prior = data.iloc[index - 1]
        extreme = min(extreme, float(row.low)) if side == "LONG" else max(extreme, float(row.high))
        aligned_body = float(row.close - row.open) * _sign(side) > 0.0
        local_control = float(row.close) > float(prior.high) if side == "LONG" else float(row.close) < float(prior.low)
        delta = _sign(side) * _finite(row.get("delta_share"), 0.0)
        progress = _sign(side) * float(row.close - row.open)
        initiative = delta > 0.0
        absorption = delta <= 0.0 and progress > 0.0
        projected = line.price_at_index(data.index, index)
        expected_side = float(row.close) >= projected if side == "LONG" else float(row.close) <= projected
        if aligned_body and local_control and expected_side and (initiative or absorption):
            stop_reference = extreme
            if mechanism == "DIAGONAL_SR_FLIP" and break_index is not None:
                wave = data.iloc[break_index:index + 1]
                stop_reference = float(wave.low.min()) if side == "LONG" else float(wave.high.max())
            return DiagonalEvent(
                mechanism=mechanism, side=side, interaction_index=start,
                break_index=break_index, hold_index=hold_index, retest_index=retest_index,
                response_index=index, event_extreme=extreme, stop_reference=stop_reference,
                projected_line=projected, penetration=abs(extreme - projected),
                response_kind="ALIGNED_INITIATIVE" if initiative else "ADVERSE_FLOW_ABSORBED",
            )
    return None


def _scan_line(data: pd.DataFrame, line: DiagonalLine, trading_start_ns: int) -> DiagonalEvent | None:
    start = max(line.observed_index_1m + 1, int(data.index.searchsorted(pd.Timestamp(trading_start_ns, unit="ns", tz="UTC"), side="left")))
    max_age = MAX_LINE_AGE_BARS[line.timeframe_minutes] * line.timeframe_minutes
    end = min(len(data), line.observed_index_1m + max_age + 1)
    defense_touches = 0
    for index in range(start, end):
        row = data.iloc[index]
        projected = line.price_at_index(data.index, index)
        tolerance = line.tolerance
        overlaps = float(row.low) <= projected + tolerance and float(row.high) >= projected - tolerance
        protected_close = float(row.close) >= projected if line.side == "SUPPORT" else float(row.close) <= projected
        outside_close = float(row.close) < projected - tolerance if line.side == "SUPPORT" else float(row.close) > projected + tolerance
        if overlaps:
            defense_touches += 1
            penetration = float(row.low) < projected - tolerance if line.side == "SUPPORT" else float(row.high) > projected + tolerance
            if protected_close and (penetration or defense_touches >= 1):
                side = "LONG" if line.side == "SUPPORT" else "SHORT"
                extreme = float(row.low if side == "LONG" else row.high)
                response = _response_at(data, index, side, line, extreme, "DIAGONAL_REJECTION")
                if response is not None:
                    return response
        if outside_close:
            if index + 1 >= end:
                return None
            hold = data.iloc[index + 1]
            projected_hold = line.price_at_index(data.index, index + 1)
            holds_outside = float(hold.close) < projected_hold - 0.25 * tolerance if line.side == "SUPPORT" else float(hold.close) > projected_hold + 0.25 * tolerance
            if not holds_outside:
                continue
            side = "SHORT" if line.side == "SUPPORT" else "LONG"
            break_index, hold_index = index, index + 1
            for retest in range(index + 2, min(end, index + 2 + MAX_RETEST_MINUTES)):
                retest_row = data.iloc[retest]
                projected_retest = line.price_at_index(data.index, retest)
                touches = float(retest_row.low) <= projected_retest + tolerance and float(retest_row.high) >= projected_retest - tolerance
                closes_new_side = float(retest_row.close) <= projected_retest if side == "SHORT" else float(retest_row.close) >= projected_retest
                if not (touches and closes_new_side):
                    continue
                wave = data.iloc[break_index:retest + 1]
                extreme = float(wave.low.min()) if side == "LONG" else float(wave.high.max())
                response = _response_at(data, retest, side, line, extreme, "DIAGONAL_SR_FLIP", break_index, hold_index, retest)
                if response is not None:
                    return response
                return None
            return None
    return None


def _external_target(levels: Sequence[LiquidityLevel], side: str, emission_index: int, entry: float, minimum_scale: int) -> LiquidityLevel | None:
    target_side = "HIGH" if side == "LONG" else "LOW"
    candidates = [
        level for level in levels
        if level.side == target_side and level.timeframe_minutes >= minimum_scale
        and level.observed_index_1m < emission_index
        and (level.first_penetration_index is None or level.first_penetration_index > emission_index)
        and ((side == "LONG" and level.price > entry) or (side == "SHORT" and level.price < entry))
    ]
    candidates.sort(key=lambda level: abs(level.price - entry))
    return candidates[0] if candidates else None


def _make_action(symbol: str, data: pd.DataFrame, line: DiagonalLine, event: DiagonalEvent, levels: Sequence[LiquidityLevel], tick: float) -> ActionSpec | None:
    emission = event.response_index
    emission_ns = _time_ns(data.index, emission)
    entry = float(data.iloc[emission].close)
    buffer = max(2.0 * tick, 0.05 * _finite(data.iloc[emission].get("prior_range_1m"), tick))
    stop = event.stop_reference - buffer if event.side == "LONG" else event.stop_reference + buffer
    if (event.side == "LONG" and stop >= entry) or (event.side == "SHORT" and stop <= entry):
        return None
    objective_kind: str
    objective_id: str
    objective_tf: int
    objective_strength: float
    target: float | None = None
    channel = line.opposite_at_index(data.index, emission)
    if event.mechanism == "DIAGONAL_REJECTION" and channel is not None and ((event.side == "LONG" and channel > entry) or (event.side == "SHORT" and channel < entry)):
        target = float(channel)
        objective_kind = f"{line.timeframe_minutes}M_PARALLEL_CHANNEL_OPPOSITE"
        objective_id = f"CHANNEL:{line.line_id}"
        objective_tf = line.timeframe_minutes
        objective_strength = float(line.opposite_pivot_inliers)
    else:
        level = _external_target(levels, event.side, emission, entry, line.timeframe_minutes)
        if level is None:
            return None
        target = float(level.price)
        objective_kind = level.source_kind
        objective_id = level.level_id
        objective_tf = level.timeframe_minutes
        objective_strength = level.strength_ratio
    economics = _economics(side=event.side, entry=entry, stop=stop, target=target, tick_size=tick, entry_style="MARKET")
    if not economics or economics["gross_rr"] < 1.0 or economics["target_net_r"] <= 0.0 or economics["stop_net_r"] >= 0.0:
        # A nearby channel edge can be too close; then use the external route.
        if objective_id.startswith("CHANNEL:"):
            level = _external_target(levels, event.side, emission, entry, line.timeframe_minutes)
            if level is None:
                return None
            target = float(level.price)
            objective_kind, objective_id = level.source_kind, level.level_id
            objective_tf, objective_strength = level.timeframe_minutes, level.strength_ratio
            economics = _economics(side=event.side, entry=entry, stop=stop, target=target, tick_size=tick, entry_style="MARKET")
        if not economics or economics["gross_rr"] < 1.0 or economics["target_net_r"] <= 0.0 or economics["stop_net_r"] >= 0.0:
            return None
    row = data.iloc[emission]
    interaction_ns = _time_ns(data.index, event.interaction_index)
    episode_id = f"DCA:{symbol}:{interaction_ns}:{line.line_id}"
    action_id = f"{episode_id}:{event.mechanism}:{event.response_kind}"
    age_minutes = (emission_ns - line.observed_time_ns) / 60_000_000_000.0
    feature_values: dict[str, Any] = {
        **economics,
        **_clock_features(pd.Timestamp(data.index[event.interaction_index])),
        **_volume_profile_features(data, emission, entry, target),
        "mechanism": event.mechanism,
        "response_kind": event.response_kind,
        "line_side": line.side,
        "line_timeframe_minutes": line.timeframe_minutes,
        "line_slope_atr_per_bar_signed": _sign(event.side) * line.slope_per_minute * line.timeframe_minutes / max(line.atr, tick),
        "line_anchor_spacing_bars": float(line.second.center_position - line.first.center_position),
        "line_age_minutes": age_minutes,
        "line_pivot_inliers": float(line.pivot_inliers),
        "line_prior_violation_fraction": line.prior_violation_fraction,
        "channel_present": float(line.channel_offset is not None),
        "channel_width_atr": float(line.channel_offset / max(line.atr, tick)) if line.channel_offset is not None else 0.0,
        "channel_opposite_pivot_inliers": float(line.opposite_pivot_inliers),
        "event_penetration_atr": event.penetration / max(line.atr, tick),
        "event_to_response_minutes": float(event.response_index - event.interaction_index),
        "decision_delta_share_signed": _sign(event.side) * _finite(row.get("delta_share"), 0.0),
        "decision_activity_ratio": _finite(row.get("activity_ratio"), 0.0),
        "decision_body_ratio": _finite(row.get("body_ratio"), 0.0),
        "decision_range_ratio": _finite(row.get("range_ratio"), 0.0),
        "decision_impact_per_activity": _finite(row.get("impact_per_activity"), 0.0),
        "decision_index_return_5m_signed": _sign(event.side) * _finite(row.get("index_return_5m"), 0.0),
        "decision_futures_return_5m_signed": _sign(event.side) * _finite(row.get("futures_return_5m"), 0.0),
        "decision_basis_change_3m_signed": _sign(event.side) * _finite(row.get("basis_change_3m_bps"), 0.0),
        "decision_oi_change_1": _finite(row.get("metric_oi_log_change_1"), 0.0),
        "decision_oi_change_3": _finite(row.get("metric_oi_log_change_3"), 0.0),
        # diagnostic-only geometry
        "diagnostic_line_first_time_ns": line.first.center_time_ns,
        "diagnostic_line_second_time_ns": line.second.center_time_ns,
        "diagnostic_line_first_price": line.first.price,
        "diagnostic_line_second_price": line.second.price,
        "diagnostic_interaction_time_ns": interaction_ns,
        "diagnostic_break_time_ns": _time_ns(data.index, event.break_index) if event.break_index is not None else np.nan,
        "diagnostic_hold_time_ns": _time_ns(data.index, event.hold_index) if event.hold_index is not None else np.nan,
        "diagnostic_retest_time_ns": _time_ns(data.index, event.retest_index) if event.retest_index is not None else np.nan,
        "diagnostic_response_time_ns": emission_ns,
        "diagnostic_event_extreme": event.event_extreme,
        "diagnostic_channel_offset": line.channel_offset if line.channel_offset is not None else np.nan,
    }
    for minutes in (1, 3, 5, 15, 30, 60):
        feature_values[f"common_return_{minutes}m_signed"] = _sign(event.side) * _finite(row.get(f"common_return_{minutes}m"), 0.0)
        feature_values[f"residual_return_{minutes}m_signed"] = _sign(event.side) * _finite(row.get(f"residual_return_{minutes}m"), 0.0)
        feature_values[f"common_breadth_{minutes}m_signed"] = _sign(event.side) * _finite(row.get(f"common_breadth_{minutes}m"), 0.0)
    for minutes in (240, 1440):
        start = max(0, emission - minutes)
        if emission > start:
            feature_values[f"htf_return_{minutes}m_signed"] = _sign(event.side) * math.log(float(data.iloc[emission].close) / float(data.iloc[start].close))
    for column in row.index:
        if str(column).startswith("metric_"):
            feature_values[str(column)] = _finite(row[column], 0.0)
    projected = line.price_at_index(data.index, event.interaction_index)
    return ActionSpec(
        action_id=action_id, episode_id=episode_id, symbol=symbol,
        event_type=event.mechanism, decision_stage=f"{event.mechanism}_{event.response_kind}",
        side=event.side, emission_index=emission, emission_time_ns=emission_ns,
        entry_style="MARKET", entry=entry, stop=stop, target=target, entry_expiry_minutes=1,
        source_level_id=line.line_id, source_kind=f"{line.timeframe_minutes}M_{line.side}_TRENDLINE",
        source_timeframe_minutes=line.timeframe_minutes,
        source_span=int(line.second.center_position - line.first.center_position),
        source_price=projected, source_lower=projected-line.tolerance, source_upper=projected+line.tolerance,
        source_strength_ratio=float(line.pivot_inliers + 0.5 * line.opposite_pivot_inliers),
        source_defense_count=line.pivot_inliers, source_age_minutes=age_minutes,
        objective_id=objective_id, objective_kind=objective_kind,
        objective_timeframe_minutes=objective_tf, objective_strength_ratio=objective_strength,
        interaction_time_ns=interaction_ns, feature_values=feature_values,
    )


def generate_symbol(symbol: str, data: pd.DataFrame, raw: pd.DataFrame, levels: Sequence[LiquidityLevel], trading_start: date):
    tick = CONTRACTS[symbol].tick_size
    pivots, aggregates = _detect_pivots(raw, data, tick)
    lines = _construct_lines(pivots, data, aggregates, tick)
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    records: list[dict[str, Any]] = []
    events = 0
    # Lines sharing the same projected boundary within one timeframe are one causal
    # episode; choose the strongest representation instead of inflating frequency.
    used: list[tuple[int, str, int, float]] = []
    for line in lines:
        event = _scan_line(data, line, start_ns)
        if event is None:
            continue
        projected = line.price_at_index(data.index, event.interaction_index)
        duplicate = any(tf == line.timeframe_minutes and side == line.side and abs(index-event.interaction_index) <= line.timeframe_minutes and abs(price-projected) <= line.tolerance for tf, side, index, price in used)
        if duplicate:
            continue
        used.append((line.timeframe_minutes, line.side, event.interaction_index, projected))
        events += 1
        action = _make_action(symbol, data, line, event, levels, tick)
        if action is None:
            continue
        label = label_action(data, action, tick)
        if label.holding_minutes is not None and label.holding_minutes > MAX_HOLD_MINUTES:
            continue
        records.append({**{key:value for key,value in asdict(action).items() if key!='feature_values'}, **action.feature_values, **asdict(label)})
    frame = pd.DataFrame(records)
    if not frame.empty and (frame.action_id.duplicated().any() or frame.episode_id.duplicated().any()):
        raise RuntimeError(f"duplicate diagonal identity {symbol}")
    return frame, {"symbol":symbol,"pivots":len(pivots),"lines":len(lines),"events":events,"actions":len(frame),"outcomes":frame.outcome.value_counts().to_dict() if not frame.empty else {}}


def run_research(*, start: date, end: date, warmup_days: int, symbols: Sequence[str], cache: Path, output: Path):
    from data_re1_flow import load_range_flow
    output.mkdir(parents=True, exist_ok=True); cache.mkdir(parents=True, exist_ok=True)
    load_start = start - timedelta(days=warmup_days)
    prepared: dict[str,pd.DataFrame] = {}; raw_by_symbol={}; levels_by_symbol={}
    for symbol in symbols:
        tick=CONTRACTS[symbol].tick_size
        raw=load_range_flow(symbol,load_start,end,cache); raw_by_symbol[symbol]=raw
        index_price=load_reference_range('indexPriceKlines',symbol,load_start,end,cache)
        mark_price=load_reference_range('markPriceKlines',symbol,load_start,end,cache)
        metrics=load_range_metrics(symbol,load_start,end,cache)
        data=prepare_market_state(raw,index_price,mark_price,metrics,tick)
        prepared[symbol]=data
        levels_by_symbol[symbol]=detect_hierarchical_liquidity(symbol,data,raw,tick)
    prepared=_add_common_state(prepared)
    frames=[];by_symbol={}
    for symbol in symbols:
        frame,summary=generate_symbol(symbol,prepared[symbol],raw_by_symbol[symbol],levels_by_symbol[symbol],start)
        by_symbol[symbol]=summary
        if not frame.empty:
            frame.to_csv(output/f'{symbol}_diagonal_actions.csv',index=False);frames.append(frame)
    combined=pd.concat(frames,ignore_index=True,sort=False) if frames else pd.DataFrame()
    combined.to_csv(output/'diagonal_actions.csv',index=False)
    resolved=combined[combined.outcome.isin(['TARGET_FIRST','STOP_FIRST','AMBIGUOUS_FILL_TARGET_SAME_MINUTE','AMBIGUOUS_SAME_MINUTE'])] if not combined.empty else combined
    summary={"start":start.isoformat(),"end":end.isoformat(),"symbols":list(symbols),"actions":len(combined),"resolved":len(resolved),"wins":int((resolved.outcome=='TARGET_FIRST').sum()) if not resolved.empty else 0,"win_rate":float((resolved.outcome=='TARGET_FIRST').mean()) if not resolved.empty else None,"by_symbol":by_symbol,"policy":POLICY,"future_information_in_features":False,"future_information_in_labels_only":True}
    (output/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return summary
