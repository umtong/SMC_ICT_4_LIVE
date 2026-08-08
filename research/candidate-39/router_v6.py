"""Candidate 39 V6: deep-value pullback and faithful ACD auction states.

V6 is a structural replacement for two V5 weaknesses:

* A first-pullback trade must reach the *deeper* of impulse-anchored VWAP and
  the 20-bar trend value before a later completed bar resumes direction.
* Opening-range trading follows an A/B/C state machine: A is established only
  after persistent acceptance beyond an objective A-distance; B invalidation
  is the far side of the opening range; C is the opposite persistent break
  after a previously established A state.

The existing liquidation-failure family is retained without loosening. All
families return audit counters so a failed replay identifies which causal gate,
not merely which numeric threshold, removed the opportunity set.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import math
from statistics import median
from typing import Callable, Mapping, Sequence

from router import BarObservation, FeatureObservation, RouteDecision
from router_v4 import SymbolContext, TraderDerivedConfig, _failed_level_candidate, _make_context
from router_v4_core import (
    FIFTEEN_MINUTES_NS,
    MINUTE_NS,
    _anchored_vwap,
    _body_fraction,
    _close_location,
    _ema,
    _ema_series,
    _finite,
    _path_efficiency,
    _safe_div,
)
import router_v5 as _v5

HOUR_NS = 60 * MINUTE_NS
DAY_NS = 24 * HOUR_NS
_EPS = 1e-12
FeatureProvider = Callable[[str, int], FeatureObservation]
Audit = Counter[str]


@dataclass(frozen=True, slots=True)
class V6Config:
    price: TraderDerivedConfig = field(default_factory=TraderDerivedConfig)
    informed: _v5.InformedRouterConfig = field(default_factory=_v5.InformedRouterConfig)
    min_route_score: float = 3.15
    ambiguity_score_gap: float = 0.28
    trend_fast_bars: int = 20
    trend_slow_bars: int = 48
    trend_slope_lookback: int = 8
    min_trend_slope_atr: float = 0.35
    impulse_window_bars: int = 16
    min_impulse_bars: int = 4
    min_impulse_atr: float = 1.60
    max_impulse_atr: float = 5.75
    min_impulse_efficiency: float = 0.36
    min_impulse_volume_ratio: float = 1.03
    min_pullback_bars: int = 2
    max_pullback_bars: int = 8
    min_retrace_fraction: float = 0.25
    max_retrace_fraction: float = 0.78
    deep_value_touch_tolerance_atr: float = 0.18
    max_close_through_slow_value_atr: float = 0.10
    min_confirmation_body_fraction: float = 0.42
    min_confirmation_close_location: float = 0.66
    stop_buffer_atr: float = 0.12
    deep_pullback_target_r_floor: float = 1.80
    session_hours: int = 8
    opening_range_minutes: int = 60
    a_fraction_of_opening_range: float = 0.25
    min_a_distance_atr: float = 0.30
    max_a_distance_atr: float = 1.10
    persistence_minutes: int = 3
    max_a_age_minutes: int = 240
    retest_tolerance_atr: float = 0.22
    min_hold_depth_atr: float = 0.05
    min_retest_body_fraction: float = 0.34
    min_retest_close_location: float = 0.60
    min_event_flow_alignment: float = 0.10
    min_confirmation_flow_alignment: float = 0.12
    min_event_oi_sponsorship: float = 0.0007
    max_confirmation_oi_contraction: float = 0.0005
    min_breadth_fraction: float = 0.50
    b_stop_buffer_atr: float = 0.10
    acd_a_target_r_floor: float = 1.45
    min_c_oi_flush: float = 0.0010
    min_c_flow_alignment: float = 0.18
    acd_c_target_r_floor: float = 1.55
    session_range_lookback: int = 6


def _num(value: float, default: float = math.nan) -> float:
    number = float(value)
    return number if math.isfinite(number) else default


def _flow_alignment(feature: FeatureObservation, side: int) -> float:
    values = []
    for value in (feature.flow_60s, feature.flow_open_10s):
        number = _num(value)
        if math.isfinite(number):
            values.append(side * number)
    return max(values) if values else -math.inf


def _feature_value(feature: FeatureObservation, name: str, default: float) -> float:
    return _num(getattr(feature, name, default), default)


def _deep_value_price_candidate(context: SymbolContext, *, breadth_by_side: Mapping[int, float], config: V6Config, audit: Audit) -> RouteDecision | None:
    audit["deep_symbol_scans"] += 1
    bars = context.bars15
    atr = context.atr
    confirmation = bars[-1]
    closes_pre = [item.close for item in bars[:-1]]
    fast = _ema(closes_pre, config.trend_fast_bars)
    slow = _ema(closes_pre, config.trend_slow_bars)
    slow_series = _ema_series(closes_pre, config.trend_slow_bars)
    if not (math.isfinite(fast) and math.isfinite(slow)) or len(slow_series) <= config.trend_slope_lookback:
        audit["deep_reject_warmup"] += 1
        return None
    slope = (slow - slow_series[-1 - config.trend_slope_lookback]) / atr
    side = 0
    if fast > slow and bars[-2].close > slow and slope >= config.min_trend_slope_atr:
        side = 1
    elif fast < slow and bars[-2].close < slow and slope <= -config.min_trend_slope_atr:
        side = -1
    if side == 0:
        audit["deep_reject_no_established_trend"] += 1
        return None

    best = None
    saw_impulse = False
    saw_deep_touch = False
    saw_confirmation = False
    for pullback_count in range(config.min_pullback_bars, config.max_pullback_bars + 1):
        pullback = list(bars[-(pullback_count + 1):-1])
        impulse_end_index = len(bars) - pullback_count - 2
        if impulse_end_index < config.impulse_window_bars:
            continue
        window_start = impulse_end_index - config.impulse_window_bars + 1
        window = list(bars[window_start:impulse_end_index + 1])
        if side > 0:
            start = min(range(len(window)), key=lambda index: window[index].low)
            if start > len(window) - config.min_impulse_bars:
                continue
            end = max(range(start + 1, len(window)), key=lambda index: window[index].high)
            if end < len(window) - 3:
                continue
            impulse = window[start:end + 1]
            impulse_start = min(item.low for item in impulse[:2])
            impulse_end = max(item.high for item in impulse[-2:])
            impulse_range = impulse_end - impulse_start
            pullback_extreme = min(item.low for item in pullback)
            retrace = _safe_div(impulse_end - pullback_extreme, impulse_range)
        else:
            start = max(range(len(window)), key=lambda index: window[index].high)
            if start > len(window) - config.min_impulse_bars:
                continue
            end = min(range(start + 1, len(window)), key=lambda index: window[index].low)
            if end < len(window) - 3:
                continue
            impulse = window[start:end + 1]
            impulse_start = max(item.high for item in impulse[:2])
            impulse_end = min(item.low for item in impulse[-2:])
            impulse_range = impulse_start - impulse_end
            pullback_extreme = max(item.high for item in pullback)
            retrace = _safe_div(pullback_extreme - impulse_end, impulse_range)
        impulse_atr = impulse_range / atr
        efficiency = _path_efficiency(impulse)
        volumes = [item.volume for item in impulse if _finite(item.volume) and item.volume > 0.0]
        impulse_volume = median(volumes) if volumes else 0.0
        volume_ratio = _safe_div(impulse_volume, context.volume_baseline)
        if not (
            config.min_impulse_atr <= impulse_atr <= config.max_impulse_atr
            and efficiency >= config.min_impulse_efficiency
            and volume_ratio >= config.min_impulse_volume_ratio
            and config.min_retrace_fraction <= retrace <= config.max_retrace_fraction
        ):
            continue
        saw_impulse = True
        anchor_index = window_start + start
        anchored = _anchored_vwap(bars[anchor_index:-1])
        if not math.isfinite(anchored):
            continue
        deep_value = min(anchored, fast) if side > 0 else max(anchored, fast)
        if side > 0:
            touched = pullback_extreme <= deep_value + config.deep_value_touch_tolerance_atr * atr
            slow_held = min(item.close for item in pullback) >= slow - config.max_close_through_slow_value_atr * atr
            confirmed = (
                confirmation.close > confirmation.open
                and confirmation.close > max(pullback[-1].high, deep_value)
                and _body_fraction(confirmation) >= config.min_confirmation_body_fraction
                and _close_location(confirmation) >= config.min_confirmation_close_location
            )
            entry = deep_value
            stop = pullback_extreme - config.stop_buffer_atr * atr
            measured_target = impulse_end + 0.50 * impulse_range
        else:
            touched = pullback_extreme >= deep_value - config.deep_value_touch_tolerance_atr * atr
            slow_held = max(item.close for item in pullback) <= slow + config.max_close_through_slow_value_atr * atr
            confirmed = (
                confirmation.close < confirmation.open
                and confirmation.close < min(pullback[-1].low, deep_value)
                and _body_fraction(confirmation) >= config.min_confirmation_body_fraction
                and _close_location(confirmation) <= 1.0 - config.min_confirmation_close_location
            )
            entry = deep_value
            stop = pullback_extreme + config.stop_buffer_atr * atr
            measured_target = impulse_end - 0.50 * impulse_range
        saw_deep_touch = saw_deep_touch or (touched and slow_held)
        saw_confirmation = saw_confirmation or confirmed
        if not (touched and slow_held and confirmed):
            continue
        risk = abs(entry - stop)
        reward = side * (measured_target - entry)
        if risk <= 0.0 or reward <= 0.0:
            audit["deep_reject_invalid_geometry"] += 1
            continue
        raw_r = reward / risk
        if raw_r < config.deep_pullback_target_r_floor:
            audit["deep_reject_insufficient_structural_space"] += 1
            continue
        score = (
            1.45
            + min(impulse_atr, 4.5) * 0.42
            + min(abs(slope), 2.5) * 0.30
            + efficiency * 0.55
            + _body_fraction(confirmation) * 0.55
            + float(breadth_by_side.get(side, 0.0)) * 0.45
            + min(raw_r, 4.0) * 0.18
        )
        decision = RouteDecision(
            symbol=context.symbol,
            state="DEEP_VALUE_PRICE_PULLBACK",
            side=side,
            score=score,
            expected_target_r=raw_r,
            atr=atr,
            entry_reference=entry,
            stop_reference=stop,
            objective_reference=measured_target,
            episode_ts=impulse[-1].ts_event,
            reasons=("ESTABLISHED_MULTI_HOUR_TREND", "FIRST_PULLBACK_TOUCHED_DEEPER_OF_AVWAP_AND_20_BAR_VALUE", "LATER_COMPLETED_15M_DIRECTION_RESUMPTION"),
            diagnostics={
                "family": "DEEP_VALUE_PRICE_PULLBACK",
                "trend_slope_atr": slope,
                "impulse_atr": impulse_atr,
                "impulse_efficiency": efficiency,
                "impulse_volume_ratio": volume_ratio,
                "pullback_bars": pullback_count,
                "retrace_fraction": retrace,
                "anchored_vwap": anchored,
                "trend_value_20": fast,
                "slow_value_48": slow,
                "deep_value": deep_value,
                "deep_value_source": "AVWAP" if deep_value == anchored else "TREND_VALUE_20",
                "pullback_extreme": pullback_extreme,
                "confirmation_body_fraction": _body_fraction(confirmation),
                "confirmation_close_location": _close_location(confirmation),
                "breadth_fraction": float(breadth_by_side.get(side, 0.0)),
                "raw_structural_r": raw_r,
                "stop_atr": risk / atr,
                "policy_target_r_floor": config.deep_pullback_target_r_floor,
                "entry_policy": "PASSIVE_DEEP_VALUE_LIMIT_AFTER_CONFIRMATION",
                "event_confirmation_separated": True,
                "non_scalping": True,
            },
        )
        audit["deep_price_candidates"] += 1
        if best is None or (decision.score, decision.expected_target_r) > (best.score, best.expected_target_r):
            best = decision
    if best is None:
        if not saw_impulse:
            audit["deep_reject_no_valid_initiative"] += 1
        elif not saw_deep_touch:
            audit["deep_reject_no_deep_value_touch"] += 1
        elif not saw_confirmation:
            audit["deep_reject_no_later_resumption"] += 1
    return best


def _informed_deep_pullback(decision: RouteDecision, *, event_feature: FeatureObservation, confirmation_feature: FeatureObservation, config: V6Config, audit: Audit) -> RouteDecision | None:
    informed = _v5._sponsored_pullback(decision, event_feature, confirmation_feature, config.informed)
    if informed is None:
        audit["deep_reject_sponsorship_state"] += 1
        return None
    data = dict(informed.diagnostics)
    data.update({
        "family": "DEEP_VALUE_SPONSORED_PULLBACK",
        "episode_key": f"{informed.symbol}:DEEP_VALUE_SPONSORED_PULLBACK:{informed.episode_ts}",
        "source_state_repair": "ACTUAL_DEEP_VALUE_TOUCH_PLUS_INITIATIVE_SPONSORSHIP",
    })
    audit["deep_survivors"] += 1
    return replace(informed, state="DEEP_VALUE_SPONSORED_PULLBACK", diagnostics=data)


def _session_start_ns(ts_event: int, session_hours: int) -> int:
    moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
    start_hour = (moment.hour // session_hours) * session_hours
    return int(datetime(moment.year, moment.month, moment.day, start_hour, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def _consecutive_persistence(minute_bars: Sequence[BarObservation], *, level: float, side: int, count: int, start_ns: int, end_ns: int) -> tuple[int, tuple[BarObservation, ...]] | None:
    selected = [item for item in minute_bars if start_ns <= item.ts_event <= end_ns]
    run = []
    for bar in selected:
        accepted = bar.close >= level if side > 0 else bar.close <= level
        if accepted:
            run.append(bar)
            if len(run) >= count:
                return run[-1].ts_event, tuple(run[-count:])
        else:
            run.clear()
    return None


def _completed_session_ranges(bars15: Sequence[BarObservation], *, current_start: int, session_hours: int, lookback: int) -> tuple[float, ...]:
    session_ns = session_hours * HOUR_NS
    ranges = []
    for offset in range(1, lookback + 1):
        start = current_start - offset * session_ns
        end = start + session_ns
        rows = [item for item in bars15 if start <= item.ts_event < end]
        minimum = max(12, session_hours * 3)
        if len(rows) >= minimum:
            ranges.append(max(item.high for item in rows) - min(item.low for item in rows))
    return tuple(ranges)


def _prior_time_objectives(bars15: Sequence[BarObservation], *, current_start: int, session_hours: int) -> tuple[float, float, float, float]:
    session_ns = session_hours * HOUR_NS
    prior_session = [item for item in bars15 if current_start - session_ns <= item.ts_event < current_start]
    moment = datetime.fromtimestamp(current_start / 1_000_000_000, tz=timezone.utc)
    day_start = int(datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    prior_day = [item for item in bars15 if day_start - DAY_NS <= item.ts_event < day_start]
    return (
        max((item.high for item in prior_session), default=math.nan),
        min((item.low for item in prior_session), default=math.nan),
        max((item.high for item in prior_day), default=math.nan),
        min((item.low for item in prior_day), default=math.nan),
    )


def _nearest_structural_target(*, side: int, entry: float, current_session_high: float, current_session_low: float, typical_session_range: float, prior_session_high: float, prior_session_low: float, prior_day_high: float, prior_day_low: float) -> tuple[float, str] | None:
    if side > 0:
        projected = current_session_low + typical_session_range
        candidates = [(prior_session_high, "PRIOR_8H_HIGH"), (prior_day_high, "PRIOR_UTC_DAY_HIGH"), (projected, "TYPICAL_8H_RANGE_COMPLETION")]
        valid = [(value, name) for value, name in candidates if math.isfinite(value) and value > entry]
        return min(valid, key=lambda item: item[0]) if valid else None
    projected = current_session_high - typical_session_range
    candidates = [(prior_session_low, "PRIOR_8H_LOW"), (prior_day_low, "PRIOR_UTC_DAY_LOW"), (projected, "TYPICAL_8H_RANGE_COMPLETION")]
    valid = [(value, name) for value, name in candidates if math.isfinite(value) and value < entry]
    return max(valid, key=lambda item: item[0]) if valid else None


def _acd_candidates(context: SymbolContext, *, minute_bars: Sequence[BarObservation], breadth_by_side: Mapping[int, float], feature_at: FeatureProvider, confirmation_feature: FeatureObservation, config: V6Config, audit: Audit) -> tuple[RouteDecision | None, RouteDecision | None]:
    audit["acd_symbol_scans"] += 1
    latest15 = context.bars15[-1]
    session_start = _session_start_ns(latest15.ts_event, config.session_hours)
    opening_end = session_start + config.opening_range_minutes * MINUTE_NS
    if latest15.ts_event < opening_end + FIFTEEN_MINUTES_NS:
        audit["acd_reject_session_not_mature"] += 1
        return None, None
    opening_minutes = [item for item in minute_bars if session_start <= item.ts_event < opening_end]
    if len(opening_minutes) < config.opening_range_minutes:
        audit["acd_reject_incomplete_opening_range"] += 1
        return None, None
    or_high = max(item.high for item in opening_minutes)
    or_low = min(item.low for item in opening_minutes)
    or_width = or_high - or_low
    if or_width <= 0.0:
        audit["acd_reject_zero_opening_range"] += 1
        return None, None
    a_distance = max(config.a_fraction_of_opening_range * or_width, config.min_a_distance_atr * context.atr)
    if a_distance / context.atr > config.max_a_distance_atr:
        audit["acd_reject_excessive_a_distance"] += 1
        return None, None
    a_up = or_high + a_distance
    a_down = or_low - a_distance
    search_end = latest15.ts_event - FIFTEEN_MINUTES_NS
    a_up_event = _consecutive_persistence(minute_bars, level=a_up, side=1, count=config.persistence_minutes, start_ns=opening_end, end_ns=search_end)
    a_down_event = _consecutive_persistence(minute_bars, level=a_down, side=-1, count=config.persistence_minutes, start_ns=opening_end, end_ns=search_end)
    established = []
    if a_up_event is not None:
        established.append((a_up_event[0], 1, a_up_event[1]))
    if a_down_event is not None:
        established.append((a_down_event[0], -1, a_down_event[1]))
    if not established:
        audit["acd_reject_no_a_establishment"] += 1
        return None, None
    established.sort(key=lambda item: item[0])
    first_a_ts, first_a_side, _first_rows = established[0]
    latest_a_ts, latest_a_side, _latest_rows = established[-1]
    session_rows = [item for item in context.bars15 if session_start <= item.ts_event <= latest15.ts_event]
    current_high = max(item.high for item in session_rows)
    current_low = min(item.low for item in session_rows)
    completed_ranges = _completed_session_ranges(context.bars15, current_start=session_start, session_hours=config.session_hours, lookback=config.session_range_lookback)
    if not completed_ranges:
        audit["acd_reject_no_completed_range_reference"] += 1
        return None, None
    typical_range = median(completed_ranges)
    prior_session_high, prior_session_low, prior_day_high, prior_day_low = _prior_time_objectives(context.bars15, current_start=session_start, session_hours=config.session_hours)

    a_decision = None
    age_minutes = (latest15.ts_event - latest_a_ts) / MINUTE_NS
    only_a_state = len(established) == 1
    a_level = a_up if latest_a_side > 0 else a_down
    if only_a_state and age_minutes <= config.max_a_age_minutes:
        if latest_a_side > 0:
            retested = latest15.low <= a_level + config.retest_tolerance_atr * context.atr
            held = latest15.close >= a_level + config.min_hold_depth_atr * context.atr
            confirmed = latest15.close > latest15.open and _close_location(latest15) >= config.min_retest_close_location
            entry = a_level
            stop = or_low - config.b_stop_buffer_atr * context.atr
        else:
            retested = latest15.high >= a_level - config.retest_tolerance_atr * context.atr
            held = latest15.close <= a_level - config.min_hold_depth_atr * context.atr
            confirmed = latest15.close < latest15.open and _close_location(latest15) <= 1.0 - config.min_retest_close_location
            entry = a_level
            stop = or_high + config.b_stop_buffer_atr * context.atr
        if retested and held and confirmed and _body_fraction(latest15) >= config.min_retest_body_fraction:
            audit["acd_a_price_candidates"] += 1
            event_feature = feature_at(context.symbol, latest_a_ts)
            event_flow = _flow_alignment(event_feature, latest_a_side)
            confirmation_flow = _flow_alignment(confirmation_feature, latest_a_side)
            event_oi = _feature_value(event_feature, "oi_change_15m", 0.0)
            confirmation_oi = _feature_value(confirmation_feature, "oi_change_15m", 0.0)
            sponsored = event_oi >= config.min_event_oi_sponsorship or event_flow >= config.min_event_flow_alignment
            breadth = float(breadth_by_side.get(latest_a_side, 0.0))
            if not (event_feature.ready and confirmation_feature.ready):
                audit["acd_a_reject_feature_readiness"] += 1
            elif not sponsored:
                audit["acd_a_reject_no_sponsorship"] += 1
            elif confirmation_flow < config.min_confirmation_flow_alignment:
                audit["acd_a_reject_confirmation_flow"] += 1
            elif confirmation_oi < -config.max_confirmation_oi_contraction:
                audit["acd_a_reject_confirmation_oi_contraction"] += 1
            elif breadth < config.min_breadth_fraction:
                audit["acd_a_reject_cross_asset_breadth"] += 1
            else:
                target_info = _nearest_structural_target(side=latest_a_side, entry=entry, current_session_high=current_high, current_session_low=current_low, typical_session_range=typical_range, prior_session_high=prior_session_high, prior_session_low=prior_session_low, prior_day_high=prior_day_high, prior_day_low=prior_day_low)
                if target_info is None:
                    audit["acd_a_reject_no_external_objective"] += 1
                else:
                    target, target_name = target_info
                    risk = abs(entry - stop)
                    reward = latest_a_side * (target - entry)
                    raw_r = _safe_div(reward, risk)
                    if raw_r < config.acd_a_target_r_floor:
                        audit["acd_a_reject_insufficient_structural_space"] += 1
                    else:
                        score = 2.20 + min(a_distance / context.atr, 1.5) * 0.40 + confirmation_flow * 0.55 + breadth * 0.45 + min(max(event_oi, 0.0) * 100.0, 0.60) + min(raw_r, 3.0) * 0.18
                        a_decision = RouteDecision(
                            symbol=context.symbol,
                            state="ACD_A_ESTABLISHMENT_RETEST",
                            side=latest_a_side,
                            score=score,
                            expected_target_r=raw_r,
                            atr=context.atr,
                            entry_reference=entry,
                            stop_reference=stop,
                            objective_reference=target,
                            episode_ts=latest_a_ts,
                            reasons=("THREE_COMPLETED_MINUTES_ESTABLISHED_A_OUTSIDE_OPENING_RANGE", "B_INVALIDATION_AT_FAR_SIDE_OF_OPENING_RANGE", "LATER_COMPLETED_15M_RETEST_HELD_A_LEVEL"),
                            diagnostics={
                                "family": "ACD_A_ESTABLISHMENT_RETEST",
                                "session_start_ns": session_start,
                                "opening_range_high": or_high,
                                "opening_range_low": or_low,
                                "opening_range_atr": or_width / context.atr,
                                "a_distance": a_distance,
                                "a_distance_atr": a_distance / context.atr,
                                "a_level": a_level,
                                "persistence_minutes": config.persistence_minutes,
                                "event_oi_change_15m": event_oi,
                                "event_flow_alignment": event_flow,
                                "confirmation_oi_change_15m": confirmation_oi,
                                "confirmation_flow_alignment": confirmation_flow,
                                "breadth_fraction": breadth,
                                "typical_session_range": typical_range,
                                "target_reference": target_name,
                                "raw_structural_r": raw_r,
                                "stop_atr": risk / context.atr,
                                "policy_target_r_floor": config.acd_a_target_r_floor,
                                "entry_policy": "PASSIVE_A_LEVEL_RETEST_LIMIT",
                                "event_confirmation_separated": True,
                                "non_scalping": True,
                                "episode_key": f"{context.symbol}:ACD_A:{session_start}:{latest_a_side}",
                            },
                        )
                        audit["acd_a_survivors"] += 1

    c_side = -first_a_side
    c_level = a_down if c_side < 0 else a_up
    c_event = _consecutive_persistence(minute_bars, level=c_level, side=c_side, count=config.persistence_minutes, start_ns=first_a_ts + MINUTE_NS, end_ns=search_end)
    c_decision = None
    if c_event is not None:
        c_ts, _c_rows = c_event
        if c_side > 0:
            retested = latest15.low <= c_level + config.retest_tolerance_atr * context.atr
            held = latest15.close >= c_level + config.min_hold_depth_atr * context.atr
            confirmed = latest15.close > latest15.open and _close_location(latest15) >= config.min_retest_close_location
            entry = c_level
            stop = or_low - config.b_stop_buffer_atr * context.atr
        else:
            retested = latest15.high >= c_level - config.retest_tolerance_atr * context.atr
            held = latest15.close <= c_level - config.min_hold_depth_atr * context.atr
            confirmed = latest15.close < latest15.open and _close_location(latest15) <= 1.0 - config.min_retest_close_location
            entry = c_level
            stop = or_high + config.b_stop_buffer_atr * context.atr
        if retested and held and confirmed and _body_fraction(latest15) >= config.min_retest_body_fraction:
            audit["acd_c_price_candidates"] += 1
            event_feature = feature_at(context.symbol, c_ts)
            confirmation_flow = _flow_alignment(confirmation_feature, c_side)
            event_flow = _flow_alignment(event_feature, c_side)
            event_oi = _feature_value(event_feature, "oi_change_15m", 0.0)
            breadth = float(breadth_by_side.get(c_side, 0.0))
            if not (event_feature.ready and confirmation_feature.ready):
                audit["acd_c_reject_feature_readiness"] += 1
            elif not (event_oi <= -config.min_c_oi_flush or event_flow >= config.min_c_flow_alignment):
                audit["acd_c_reject_no_failed_a_transition"] += 1
            elif confirmation_flow < config.min_c_flow_alignment:
                audit["acd_c_reject_confirmation_flow"] += 1
            elif breadth < config.min_breadth_fraction:
                audit["acd_c_reject_cross_asset_breadth"] += 1
            else:
                target_info = _nearest_structural_target(side=c_side, entry=entry, current_session_high=current_high, current_session_low=current_low, typical_session_range=typical_range, prior_session_high=prior_session_high, prior_session_low=prior_session_low, prior_day_high=prior_day_high, prior_day_low=prior_day_low)
                if target_info is None:
                    audit["acd_c_reject_no_external_objective"] += 1
                else:
                    target, target_name = target_info
                    risk = abs(entry - stop)
                    reward = c_side * (target - entry)
                    raw_r = _safe_div(reward, risk)
                    if raw_r < config.acd_c_target_r_floor:
                        audit["acd_c_reject_insufficient_structural_space"] += 1
                    else:
                        score = 2.45 + confirmation_flow * 0.60 + breadth * 0.45 + min(abs(min(event_oi, 0.0)) * 100.0, 0.70) + min(raw_r, 3.0) * 0.20
                        c_decision = RouteDecision(
                            symbol=context.symbol,
                            state="ACD_C_FAILED_A_REVERSAL",
                            side=c_side,
                            score=score,
                            expected_target_r=raw_r,
                            atr=context.atr,
                            entry_reference=entry,
                            stop_reference=stop,
                            objective_reference=target,
                            episode_ts=c_ts,
                            reasons=("PRIOR_A_STATE_WAS_ESTABLISHED", "OPPOSITE_C_LEVEL_PERSISTED_FOR_THREE_COMPLETED_MINUTES", "LATER_COMPLETED_15M_RETEST_HELD_C_LEVEL"),
                            diagnostics={
                                "family": "ACD_C_FAILED_A_REVERSAL",
                                "session_start_ns": session_start,
                                "first_a_side": first_a_side,
                                "first_a_ts": first_a_ts,
                                "opening_range_high": or_high,
                                "opening_range_low": or_low,
                                "a_distance": a_distance,
                                "c_level": c_level,
                                "event_oi_change_15m": event_oi,
                                "event_flow_alignment": event_flow,
                                "confirmation_flow_alignment": confirmation_flow,
                                "breadth_fraction": breadth,
                                "typical_session_range": typical_range,
                                "target_reference": target_name,
                                "raw_structural_r": raw_r,
                                "stop_atr": risk / context.atr,
                                "policy_target_r_floor": config.acd_c_target_r_floor,
                                "entry_policy": "PASSIVE_C_LEVEL_RETEST_LIMIT",
                                "event_confirmation_separated": True,
                                "non_scalping": True,
                                "episode_key": f"{context.symbol}:ACD_C:{session_start}:{c_side}",
                            },
                        )
                        audit["acd_c_survivors"] += 1
    return a_decision, c_decision


def route_v6_universe(*, minute_bars_by_symbol: Mapping[str, Sequence[BarObservation]], confirmation_features_by_symbol: Mapping[str, FeatureObservation], feature_at: FeatureProvider, config: V6Config | None = None) -> tuple[RouteDecision | None, dict[str, RouteDecision], dict[str, int]]:
    cfg = config or V6Config()
    audit = Counter()
    contexts = {}
    for symbol, bars in minute_bars_by_symbol.items():
        context = _make_context(symbol, bars, cfg.price)
        if context is None:
            audit["context_not_ready"] += 1
        else:
            contexts[symbol] = context
    if not contexts:
        return None, {}, dict(audit)
    returns = [item.return_4h_atr for item in contexts.values()]
    median_return = median(returns)
    breadth_by_side = {
        1: sum(value > 0.15 for value in returns) / len(returns),
        -1: sum(value < -0.15 for value in returns) / len(returns),
    }
    decisions = {}
    for symbol, context in contexts.items():
        confirmation = confirmation_features_by_symbol.get(symbol, FeatureObservation(0, ready=False))
        candidates = []
        deep_price = _deep_value_price_candidate(context, breadth_by_side=breadth_by_side, config=cfg, audit=audit)
        if deep_price is not None:
            informed = _informed_deep_pullback(deep_price, event_feature=feature_at(symbol, deep_price.episode_ts), confirmation_feature=confirmation, config=cfg, audit=audit)
            if informed is not None:
                candidates.append(informed)
        failed_price = _failed_level_candidate(context, peer_breadth_by_side=breadth_by_side, config=cfg.price)
        if failed_price is not None:
            audit["liquidation_price_candidates"] += 1
            informed_failed = _v5._informed_failed_level(failed_price, context, feature_at(symbol, failed_price.episode_ts), confirmation, median_return, breadth_by_side, cfg.informed)
            if informed_failed is None:
                audit["liquidation_reject_state_transition"] += 1
            else:
                audit["liquidation_survivors"] += 1
                candidates.append(informed_failed)
        acd_a, acd_c = _acd_candidates(context, minute_bars=minute_bars_by_symbol[symbol], breadth_by_side=breadth_by_side, feature_at=feature_at, confirmation_feature=confirmation, config=cfg, audit=audit)
        candidates.extend(item for item in (acd_a, acd_c) if item is not None)
        if not candidates:
            continue
        selected = max(candidates, key=lambda item: (item.score, item.expected_target_r, item.state))
        if selected.score >= cfg.min_route_score:
            decisions[symbol] = selected
        else:
            audit["route_reject_score"] += 1
    ranked = sorted(decisions.values(), key=lambda item: (item.score, item.expected_target_r, item.symbol == "BTCUSDT", item.symbol), reverse=True)
    if not ranked:
        return None, decisions, dict(audit)
    if len(ranked) > 1 and ranked[0].side != ranked[1].side and ranked[0].score - ranked[1].score < cfg.ambiguity_score_gap:
        audit["route_global_ambiguity"] += 1
        return None, decisions, dict(audit)
    audit["route_winner"] += 1
    return ranked[0], decisions, dict(audit)
