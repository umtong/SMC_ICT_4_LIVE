"""Independent trader-derived scenario families for Candidate 39 V4."""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Mapping, Sequence

from router import BarObservation, RouteDecision
from router_v4_core import (
    MINUTE_NS,
    LevelReference,
    SymbolContext,
    TraderDerivedConfig,
    _anchored_vwap,
    _body_fraction,
    _close_location,
    _decision,
    _ema,
    _ema_series,
    _finite,
    _path_efficiency,
    _safe_div,
)

def _first_pullback_candidate(
    context: SymbolContext,
    *,
    peer_breadth: float,
    config: TraderDerivedConfig,
) -> RouteDecision | None:
    bars = context.bars15
    atr = context.atr
    confirmation = bars[-1]
    closes_pre = [item.close for item in bars[:-1]]
    ema_fast_now = _ema(closes_pre, config.trend_fast_bars)
    ema_slow_now = _ema(closes_pre, config.trend_slow_bars)
    if not (_finite(ema_fast_now) and _finite(ema_slow_now)):
        return None

    ema_slow_series = _ema_series(closes_pre, config.trend_slow_bars)
    if len(ema_slow_series) <= config.trend_slope_lookback:
        return None
    slow_then = ema_slow_series[-1 - config.trend_slope_lookback]
    slope_atr = (ema_slow_now - slow_then) / atr
    trend_side = 0
    if (
        ema_fast_now > ema_slow_now
        and bars[-2].close > ema_slow_now
        and slope_atr >= config.min_trend_slope_atr
    ):
        trend_side = 1
    elif (
        ema_fast_now < ema_slow_now
        and bars[-2].close < ema_slow_now
        and slope_atr <= -config.min_trend_slope_atr
    ):
        trend_side = -1
    if trend_side == 0:
        return None

    best: RouteDecision | None = None
    for pullback_count in range(config.min_pullback_bars, config.max_pullback_bars + 1):
        pullback = list(bars[-(pullback_count + 1) : -1])
        impulse_end = len(bars) - pullback_count - 2
        if impulse_end < config.impulse_window_bars:
            continue
        window_start = impulse_end - config.impulse_window_bars + 1
        impulse_window = list(bars[window_start : impulse_end + 1])

        if trend_side > 0:
            start_offset = min(
                range(len(impulse_window)),
                key=lambda index: impulse_window[index].low,
            )
            if start_offset > len(impulse_window) - config.min_impulse_bars:
                continue
            end_offset = max(
                range(start_offset + 1, len(impulse_window)),
                key=lambda index: impulse_window[index].high,
            )
            if end_offset < len(impulse_window) - 3:
                continue
            impulse = impulse_window[start_offset : end_offset + 1]
            impulse_start = min(item.low for item in impulse[:2])
            impulse_end_price = max(item.high for item in impulse[-2:])
            impulse_range = impulse_end_price - impulse_start
            pullback_extreme = min(item.low for item in pullback)
            retrace = _safe_div(impulse_end_price - pullback_extreme, impulse_range)
        else:
            start_offset = max(
                range(len(impulse_window)),
                key=lambda index: impulse_window[index].high,
            )
            if start_offset > len(impulse_window) - config.min_impulse_bars:
                continue
            end_offset = min(
                range(start_offset + 1, len(impulse_window)),
                key=lambda index: impulse_window[index].low,
            )
            if end_offset < len(impulse_window) - 3:
                continue
            impulse = impulse_window[start_offset : end_offset + 1]
            impulse_start = max(item.high for item in impulse[:2])
            impulse_end_price = min(item.low for item in impulse[-2:])
            impulse_range = impulse_start - impulse_end_price
            pullback_extreme = max(item.high for item in pullback)
            retrace = _safe_div(pullback_extreme - impulse_end_price, impulse_range)

        impulse_atr = impulse_range / atr
        if impulse_atr < config.min_impulse_atr:
            continue
        if not (config.min_retrace_fraction <= retrace <= config.max_retrace_fraction):
            continue
        efficiency = _path_efficiency(impulse)
        if efficiency < config.min_impulse_efficiency:
            continue
        impulse_volume = median(
            [item.volume for item in impulse if _finite(item.volume) and item.volume > 0.0]
        ) if any(item.volume > 0.0 for item in impulse) else 0.0
        volume_ratio = _safe_div(impulse_volume, context.volume_baseline)
        if volume_ratio < config.min_impulse_volume_ratio:
            continue

        anchor_start = window_start + start_offset
        anchored = _anchored_vwap(bars[anchor_start:-1])
        if not _finite(anchored):
            continue
        dynamic_value = max(anchored, ema_fast_now) if trend_side > 0 else min(anchored, ema_fast_now)
        pullback_volume = median(
            [item.volume for item in pullback if _finite(item.volume) and item.volume > 0.0]
        ) if any(item.volume > 0.0 for item in pullback) else impulse_volume
        contraction = _safe_div(pullback_volume, impulse_volume, 1.0)

        body = _body_fraction(confirmation)
        close_loc = _close_location(confirmation)
        previous = pullback[-1]
        if trend_side > 0:
            touched_value = pullback_extreme <= dynamic_value + config.value_touch_tolerance_atr * atr
            value_held = (
                min(item.close for item in pullback) > ema_slow_now - 0.10 * atr
                and pullback_extreme > impulse_start
            )
            confirmed = (
                confirmation.close > confirmation.open
                and confirmation.close > previous.close
                and confirmation.close >= dynamic_value + 0.02 * atr
                and body >= config.min_confirmation_body_fraction
                and close_loc >= config.min_confirmation_close_location
            )
            if not (touched_value and value_held and confirmed):
                continue
            entry = max(dynamic_value, confirmation.close - 0.16 * atr)
            entry = min(entry, confirmation.close)
            stop = pullback_extreme - config.pullback_stop_buffer_atr * atr
            risk = entry - stop
            target = max(
                impulse_end_price + 0.35 * impulse_range,
                entry + config.continuation_target_r * risk,
            )
        else:
            touched_value = pullback_extreme >= dynamic_value - config.value_touch_tolerance_atr * atr
            value_held = (
                max(item.close for item in pullback) < ema_slow_now + 0.10 * atr
                and pullback_extreme < impulse_start
            )
            confirmed = (
                confirmation.close < confirmation.open
                and confirmation.close < previous.close
                and confirmation.close <= dynamic_value - 0.02 * atr
                and body >= config.min_confirmation_body_fraction
                and close_loc <= 1.0 - config.min_confirmation_close_location
            )
            if not (touched_value and value_held and confirmed):
                continue
            entry = min(dynamic_value, confirmation.close + 0.16 * atr)
            entry = max(entry, confirmation.close)
            stop = pullback_extreme + config.pullback_stop_buffer_atr * atr
            risk = stop - entry
            target = min(
                impulse_end_price - 0.35 * impulse_range,
                entry - config.continuation_target_r * risk,
            )

        if risk <= 0.0:
            continue
        score = (
            1.20
            + min(impulse_atr, 4.0) * 0.45
            + min(abs(slope_atr), 2.5) * 0.35
            + efficiency * 0.70
            + body * 0.55
            + max(0.0, 1.15 - contraction) * 0.35
            + peer_breadth * 0.45
        )
        decision = _decision(
            context=context,
            state="FIRST_PULLBACK_CONTINUATION",
            side=trend_side,
            score=score,
            entry=entry,
            stop=stop,
            target=target,
            episode_ts=impulse[-1].ts_event,
            reasons=(
                "MULTI_HOUR_INITIATIVE",
                "FIRST_CONTROLLED_PULLBACK_TO_DYNAMIC_VALUE",
                "SEPARATE_15M_VALUE_HOLD_CONFIRMATION",
            ),
            diagnostics={
                "_min_stop_atr": config.min_stop_atr,
                "_max_stop_atr": config.max_stop_atr,
                "family": "FIRST_PULLBACK_CONTINUATION",
                "trend_slope_atr": slope_atr,
                "impulse_atr": impulse_atr,
                "impulse_efficiency": efficiency,
                "impulse_volume_ratio": volume_ratio,
                "pullback_bars": pullback_count,
                "retrace_fraction": retrace,
                "pullback_volume_contraction": contraction,
                "anchored_vwap": anchored,
                "ema_fast": ema_fast_now,
                "ema_slow": ema_slow_now,
                "peer_breadth": peer_breadth,
                "confirmation_body_fraction": body,
                "confirmation_close_location": close_loc,
            },
            policy_floor=config.continuation_target_r,
        )
        if decision is not None and (best is None or decision.score > best.score):
            best = decision
    return best


def _utc_day_bounds(ts_event: int) -> tuple[int, int]:
    moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
    day_start = int(
        datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc).timestamp()
        * 1_000_000_000
    )
    return day_start - 24 * 60 * MINUTE_NS, day_start


def _session_bounds(ts_event: int) -> tuple[int, int]:
    moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
    day_start = int(
        datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc).timestamp()
        * 1_000_000_000
    )
    current_session = (moment.hour // 8) * 8 * 60 * MINUTE_NS
    end_ns = day_start + current_session
    if end_ns == day_start:
        end_ns = day_start
    return end_ns - 8 * 60 * MINUTE_NS, end_ns


def _level_references(bars: Sequence[BarObservation], ts_event: int) -> tuple[LevelReference, ...]:
    references: list[LevelReference] = []
    for name, (start_ns, end_ns) in (
        ("PRIOR_UTC_DAY", _utc_day_bounds(ts_event)),
        ("PRIOR_8H_SESSION", _session_bounds(ts_event)),
    ):
        selected = [item for item in bars if start_ns <= item.ts_event < end_ns]
        minimum = 80 if name == "PRIOR_UTC_DAY" else 24
        if len(selected) < minimum:
            continue
        references.append(
            LevelReference(
                name=name,
                high=max(item.high for item in selected),
                low=min(item.low for item in selected),
                start_ns=start_ns,
                end_ns=end_ns,
            )
        )
    return tuple(references)


def _failed_level_candidate(
    context: SymbolContext,
    *,
    peer_breadth_by_side: Mapping[int, float],
    config: TraderDerivedConfig,
) -> RouteDecision | None:
    bars = context.bars15
    atr = context.atr
    confirmation = bars[-1]
    best: RouteDecision | None = None
    references = _level_references(bars[:-1], confirmation.ts_event)
    if not references:
        return None

    event_start = max(0, len(bars) - 1 - config.failed_break_event_lookback)
    candidate_events = list(enumerate(bars[event_start:-1], start=event_start))
    for reference in references:
        midpoint = (reference.high + reference.low) / 2.0
        range_size = reference.high - reference.low
        if range_size <= atr:
            continue
        for event_index, event in candidate_events:
            later = bars[event_index + 1 : -1]
            for attacked_side, level in ((1, reference.high), (-1, reference.low)):
                if attacked_side > 0:
                    sweep_atr = (event.high - level) / atr
                    reaccepted = event.close <= level - config.min_reaccept_depth_atr * atr
                    later_inside = all(item.close <= level + config.max_confirmation_extension_atr * atr for item in later)
                    retested = confirmation.high >= level - config.retest_tolerance_atr * atr
                    no_new_attack = confirmation.high <= event.high + config.max_confirmation_extension_atr * atr
                    confirmed = (
                        confirmation.close < confirmation.open
                        and confirmation.close < level
                        and _body_fraction(confirmation) >= config.min_confirmation_body_fraction
                        and _close_location(confirmation) <= 0.42
                    )
                    side = -1
                    entry = level - 0.04 * atr
                    entry = max(entry, confirmation.close)
                    stop = max(event.high, confirmation.high) + config.failed_break_stop_buffer_atr * atr
                    target = midpoint
                else:
                    sweep_atr = (level - event.low) / atr
                    reaccepted = event.close >= level + config.min_reaccept_depth_atr * atr
                    later_inside = all(item.close >= level - config.max_confirmation_extension_atr * atr for item in later)
                    retested = confirmation.low <= level + config.retest_tolerance_atr * atr
                    no_new_attack = confirmation.low >= event.low - config.max_confirmation_extension_atr * atr
                    confirmed = (
                        confirmation.close > confirmation.open
                        and confirmation.close > level
                        and _body_fraction(confirmation) >= config.min_confirmation_body_fraction
                        and _close_location(confirmation) >= 0.58
                    )
                    side = 1
                    entry = level + 0.04 * atr
                    entry = min(entry, confirmation.close)
                    stop = min(event.low, confirmation.low) - config.failed_break_stop_buffer_atr * atr
                    target = midpoint

                if not (
                    config.min_sweep_atr <= sweep_atr <= config.max_sweep_atr
                    and reaccepted
                    and later_inside
                    and retested
                    and no_new_attack
                    and confirmed
                ):
                    continue
                risk = abs(entry - stop)
                reward = side * (target - entry)
                raw_r = _safe_div(reward, risk)
                if raw_r < config.reversal_target_r_floor:
                    # The midpoint is the honest same-range objective.  Do not
                    # manufacture a farther target merely to rescue geometry.
                    continue
                body = _body_fraction(confirmation)
                peer_breadth = float(peer_breadth_by_side.get(side, 0.0))
                score = (
                    1.35
                    + min(sweep_atr, 1.5) * 0.65
                    + body * 0.75
                    + min(raw_r, 3.0) * 0.35
                    + peer_breadth * 0.30
                    + (0.25 if reference.name == "PRIOR_UTC_DAY" else 0.0)
                )
                decision = _decision(
                    context=context,
                    state="FAILED_LEVEL_REACCEPTANCE",
                    side=side,
                    score=score,
                    entry=entry,
                    stop=stop,
                    target=target,
                    episode_ts=event.ts_event,
                    reasons=(
                        f"{reference.name}_LIQUIDITY_ATTACK",
                        "EVENT_BAR_CLOSED_BACK_INSIDE",
                        "LATER_15M_RETEST_REJECTED_ATTACKED_LEVEL",
                    ),
                    diagnostics={
                        "_min_stop_atr": config.min_stop_atr,
                        "_max_stop_atr": config.max_stop_atr,
                        "family": "FAILED_LEVEL_REACCEPTANCE",
                        "reference": reference.name,
                        "attacked_level": level,
                        "reference_midpoint": midpoint,
                        "reference_range_atr": range_size / atr,
                        "sweep_atr": sweep_atr,
                        "confirmation_body_fraction": body,
                        "peer_breadth": peer_breadth,
                    },
                    policy_floor=config.reversal_target_r_floor,
                )
                if decision is not None and (best is None or decision.score > best.score):
                    best = decision
    return best
