"""Leadership-transfer and failed-reentry router for Candidate 41.

Candidate 41 deliberately avoids two already-rejected shortcuts:

* it does not chase every accepted range break (Candidate 39), and
* it does not fade a high-effort/low-progress bar immediately (Candidate 16).

It recognizes two complete intraday scenario families from a common four-asset
clock:

``LEADER_FIRST_PULLBACK_CONTINUATION``
    A fresh 15-minute repricing leg establishes the strongest cross-asset
    leader.  The first six-minute response must be a shallow pullback followed
    by renewed initiative.  Only the actual leader is eligible.

``MATURE_EXTENSION_FAILED_REENTRY``
    A mature one-hour trend probes beyond its prior extreme, cracks back into
    value, attempts to re-enter the extension, fails, and resumes in the
    opposite direction.  The sequential crack -> failed re-entry -> renewed
    opposite initiative is mandatory; a wick/divergence alone is never enough.

The router owns market-state classification and structural geometry only.
NautilusTrader owns matching, fills, fees, contingent orders, positions and
continuous-account NAV.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from statistics import median
from typing import Mapping, Sequence

_EPS = 1e-12


def _finite(value: float, default: float = math.nan) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _sign(value: float, deadband: float = 0.0) -> int:
    if value > deadband:
        return 1
    if value < -deadband:
        return -1
    return 0


def _safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    if not math.isfinite(denominator) or abs(denominator) <= _EPS:
        return default
    return numerator / denominator


@dataclass(frozen=True, slots=True)
class BarObservation:
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    observed_time_ns: int
    ready: bool = True
    flow_open_10s: float = math.nan
    notional_open_10s_burst: float = math.nan
    flow_60s: float = math.nan
    efficiency_60s: float = math.nan
    oi_change_15m: float = math.nan
    premium_z: float = math.nan


@dataclass(frozen=True, slots=True)
class RouteConfig:
    # Fields populated by the reused Candidate 35 StrategyConfig.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.78
    min_impulse_atr_reversal: float = 0.90
    min_response_atr: float = 0.08
    min_participation_ratio: float = 1.08
    min_route_score: float = 4.10
    ambiguity_score_gap: float = 0.40
    continuation_target_r: float = 1.80
    reversal_target_r: float = 1.50

    # Structural windows.  Six response bars are required to separate an
    # interaction from the later confirmation/failure sequence.
    context_bars: int = 60
    prior_bars: int = 15
    response_bars: int = 6
    entry_validity_minutes: int = 10

    # Shared data-quality and geometry rules.
    max_abs_premium_z: float = 4.00
    min_context_range_atr: float = 1.10
    max_context_range_atr: float = 8.50
    stop_buffer_atr: float = 0.08
    min_stop_atr: float = 0.28
    max_stop_atr: float = 2.60
    min_geometry_r: float = 1.10

    # Fresh leader / first pullback continuation.
    min_break_close_atr: float = 0.05
    max_fresh_break_extension_atr: float = 1.55
    min_impulse_close_location: float = 0.68
    min_impulse_path_efficiency: float = 0.42
    min_leader_excess_atr: float = 0.16
    min_peer_breadth_fraction: float = 0.50
    min_pullback_fraction: float = 0.07
    max_pullback_fraction: float = 0.54
    min_first_leg_against_atr: float = 0.035
    min_reacceleration_atr: float = 0.055
    min_confirmation_flow: float = 0.030
    min_oi_change_for_leader: float = -0.0040
    continuation_extension_fraction: float = 0.72
    continuation_extension_cap_atr: float = 1.80

    # Mature extension -> crack -> failed re-entry -> reversal.
    min_mature_context_trend_atr: float = 1.55
    min_mature_context_range_atr: float = 2.10
    min_mature_close_location: float = 0.64
    min_probe_extension_atr: float = 0.08
    max_probe_close_retention: float = 0.52
    min_first_crack_atr: float = 0.14
    min_crack_inside_atr: float = 0.035
    reentry_reach_tolerance_atr: float = 0.14
    max_reentry_acceptance_atr: float = 0.035
    min_failure_resume_atr: float = 0.055
    min_reversal_flow: float = 0.030
    max_oi_build_on_failed_reentry: float = 0.0040


@dataclass(frozen=True, slots=True)
class RouteDecision:
    symbol: str
    state: str
    side: int
    score: float
    expected_target_r: float
    atr: float
    entry_reference: float
    stop_reference: float
    objective_reference: float
    episode_ts: int
    reasons: tuple[str, ...] = ()
    diagnostics: Mapping[str, float | int | bool | str] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.state != "UNRESOLVED" and self.side in (-1, 1)


def true_range(current: BarObservation, previous_close: float) -> float:
    return max(
        current.high - current.low,
        abs(current.high - previous_close),
        abs(current.low - previous_close),
    )


def causal_atr(bars: Sequence[BarObservation], period: int) -> float:
    if period < 2 or len(bars) < period + 1:
        return math.nan
    selected = bars[-(period + 1) :]
    values = [
        true_range(selected[index], selected[index - 1].close)
        for index in range(1, len(selected))
    ]
    clean = [value for value in values if math.isfinite(value) and value > 0.0]
    return sum(clean) / len(clean) if clean else math.nan


def _volume_ratio(
    baseline: Sequence[BarObservation],
    event: Sequence[BarObservation],
) -> float:
    reference = [bar.volume for bar in baseline if math.isfinite(bar.volume) and bar.volume > 0.0]
    current = [bar.volume for bar in event if math.isfinite(bar.volume) and bar.volume > 0.0]
    if not reference or not current:
        return 0.0
    return (sum(current) / len(current)) / max(median(reference), _EPS)


def _path_efficiency(bars: Sequence[BarObservation]) -> float:
    if not bars:
        return 0.0
    path = sum(max(bar.high - bar.low, 0.0) for bar in bars)
    return abs(bars[-1].close - bars[0].open) / max(path, _EPS)


def _directional_close_location(
    *, side: int, high: float, low: float, close: float
) -> float:
    span = max(high - low, _EPS)
    return (close - low) / span if side > 0 else (high - close) / span


def _unresolved(
    symbol: str,
    reason: str,
    context: Mapping[str, float | int | bool | str] | None = None,
) -> RouteDecision:
    diagnostics = dict(context or {})
    return RouteDecision(
        symbol=symbol,
        state="UNRESOLVED",
        side=0,
        score=0.0,
        expected_target_r=0.0,
        atr=_finite(diagnostics.get("atr", math.nan)),
        entry_reference=_finite(diagnostics.get("entry", math.nan)),
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(diagnostics.get("episode_ts", 0)),
        reasons=(reason,),
        diagnostics=diagnostics,
    )


def _extract_context(
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig,
) -> dict[str, float | int | bool | str]:
    required = max(
        config.context_bars + config.prior_bars + config.response_bars,
        config.atr_period + config.prior_bars + config.response_bars + 1,
    )
    if len(bars) < required:
        raise ValueError(f"NEED_{required}_COMPLETED_BARS")

    response = list(bars[-config.response_bars :])
    impulse = list(
        bars[-(config.prior_bars + config.response_bars) : -config.response_bars]
    )
    context = list(
        bars[
            -(config.context_bars + config.prior_bars + config.response_bars) :
            -(config.prior_bars + config.response_bars)
        ]
    )
    atr_source = bars[: -(config.prior_bars + config.response_bars)]
    atr = causal_atr(atr_source, config.atr_period)
    if not math.isfinite(atr) or atr <= 0.0:
        raise ValueError("CAUSAL_ATR_UNAVAILABLE")

    context_high = max(bar.high for bar in context)
    context_low = min(bar.low for bar in context)
    context_mid = (context_high + context_low) / 2.0
    context_range = context_high - context_low
    context_change = context[-1].close - context[0].open
    context_side = _sign(context_change, 0.20 * atr)
    context_trend_atr = context_change / atr
    context_close_location = (
        _directional_close_location(
            side=context_side,
            high=context_high,
            low=context_low,
            close=context[-1].close,
        )
        if context_side
        else 0.50
    )

    impulse_open = impulse[0].open
    impulse_close = impulse[-1].close
    impulse_high = max(bar.high for bar in impulse)
    impulse_low = min(bar.low for bar in impulse)
    impulse_change = impulse_close - impulse_open
    impulse_side = _sign(impulse_change, 0.04 * atr)
    impulse_range = max(impulse_high - impulse_low, _EPS)
    impulse_atr = abs(impulse_change) / atr
    impulse_close_location = (
        _directional_close_location(
            side=impulse_side,
            high=impulse_high,
            low=impulse_low,
            close=impulse_close,
        )
        if impulse_side
        else 0.50
    )
    impulse_efficiency = _path_efficiency(impulse)
    boundary = context_high if impulse_side > 0 else context_low
    impulse_extreme = impulse_high if impulse_side > 0 else impulse_low
    break_close_atr = (
        impulse_side * (impulse_close - boundary) / atr if impulse_side else 0.0
    )
    break_extreme_atr = (
        impulse_side * (impulse_extreme - boundary) / atr if impulse_side else 0.0
    )
    break_retention = _safe_ratio(
        max(break_close_atr, 0.0),
        max(break_extreme_atr, 0.0),
        0.0,
    )

    first_half = response[: max(2, len(response) // 2)]
    second_half = response[max(2, len(response) // 2) :]
    response_high = max(bar.high for bar in response)
    response_low = min(bar.low for bar in response)
    response_close = response[-1].close
    response_extreme_against = response_low if impulse_side > 0 else response_high
    pullback_distance = (
        impulse_side * (impulse_extreme - response_extreme_against)
        if impulse_side
        else 0.0
    )
    pullback_fraction = _safe_ratio(pullback_distance, impulse_range, 0.0)
    first_leg_against_atr = (
        -impulse_side * (first_half[-1].close - first_half[0].open) / atr
        if impulse_side and first_half
        else 0.0
    )
    second_leg_with_atr = (
        impulse_side * (second_half[-1].close - second_half[0].open) / atr
        if impulse_side and second_half
        else 0.0
    )
    final_bar_with = (
        impulse_side * (response[-1].close - response[-1].open) / atr
        if impulse_side
        else 0.0
    )
    response_retention_atr = (
        impulse_side * (response_close - boundary) / atr if impulse_side else 0.0
    )
    impulse_mid = (impulse_open + impulse_extreme) / 2.0
    pullback_holds_impulse_mid = (
        impulse_side * (response_extreme_against - impulse_mid) >= 0.0
        if impulse_side
        else False
    )

    # The mature-failure sequence deliberately uses different response slices:
    # first three bars create the crack; bars four/five attempt the re-entry;
    # the sixth bar must resume away from the failed boundary.
    crack = response[:3]
    reentry = response[3:5]
    final = response[-1]
    trend_side = context_side
    trend_boundary = context_high if trend_side > 0 else context_low
    trend_probe_extreme = (
        max(impulse_high, response_high)
        if trend_side > 0
        else min(impulse_low, response_low)
    )
    probe_extension_atr = (
        trend_side * (impulse_extreme - trend_boundary) / atr if trend_side else 0.0
    )
    probe_close_retention = _safe_ratio(
        max(trend_side * (impulse_close - trend_boundary) / atr, 0.0),
        max(probe_extension_atr, 0.0),
        0.0,
    )
    first_crack_atr = (
        -trend_side * (crack[-1].close - crack[0].open) / atr
        if trend_side and crack
        else 0.0
    )
    crack_inside_atr = (
        -trend_side * (crack[-1].close - trend_boundary) / atr
        if trend_side and crack
        else 0.0
    )
    reentry_extreme = (
        max(bar.high for bar in reentry)
        if trend_side > 0 and reentry
        else min(bar.low for bar in reentry)
        if trend_side < 0 and reentry
        else trend_boundary
    )
    reentry_reach_atr = (
        trend_side * (reentry_extreme - trend_boundary) / atr if trend_side else 0.0
    )
    reentry_close = reentry[-1].close if reentry else final.open
    reentry_acceptance_atr = (
        trend_side * (reentry_close - trend_boundary) / atr if trend_side else 0.0
    )
    failure_resume_atr = (
        -trend_side * (final.close - final.open) / atr if trend_side else 0.0
    )
    failure_progress_from_reentry_atr = (
        -trend_side * (final.close - reentry_close) / atr if trend_side else 0.0
    )

    # Stable causal episode identifier: all repeated route checks sharing the
    # same one-hour trend origin are the same opportunity, not new trades.
    if impulse_side > 0:
        origin_bar = min(context, key=lambda bar: (bar.low, bar.ts_event))
    elif impulse_side < 0:
        origin_bar = max(context, key=lambda bar: (bar.high, -bar.ts_event))
    else:
        origin_bar = context[0]
    causal_episode_id = f"{impulse_side}:{origin_bar.ts_event}"

    opening_flow = _finite(feature.flow_open_10s, 0.0)
    interaction_flow = _finite(feature.flow_60s, 0.0)
    oi_change = _finite(feature.oi_change_15m)
    premium_z = _finite(feature.premium_z, 0.0)
    participation = max(
        _finite(feature.notional_open_10s_burst, 0.0),
        _volume_ratio(context[-min(30, len(context)) :], impulse),
    )

    return {
        "atr": atr,
        "episode_ts": response[-1].ts_event,
        "causal_episode_id": causal_episode_id,
        "origin_ts": origin_bar.ts_event,
        "context_high": context_high,
        "context_low": context_low,
        "context_mid": context_mid,
        "context_range_atr": context_range / atr,
        "context_side": context_side,
        "context_trend_atr": context_trend_atr,
        "context_close_location": context_close_location,
        "impulse_open": impulse_open,
        "impulse_close": impulse_close,
        "impulse_high": impulse_high,
        "impulse_low": impulse_low,
        "impulse_range": impulse_range,
        "impulse_side": impulse_side,
        "impulse_atr": impulse_atr,
        "impulse_close_location": impulse_close_location,
        "impulse_path_efficiency": impulse_efficiency,
        "boundary": boundary,
        "impulse_extreme": impulse_extreme,
        "break_close_atr": break_close_atr,
        "break_extreme_atr": break_extreme_atr,
        "break_retention": break_retention,
        "response_high": response_high,
        "response_low": response_low,
        "response_close": response_close,
        "response_retention_atr": response_retention_atr,
        "pullback_fraction": pullback_fraction,
        "first_leg_against_atr": first_leg_against_atr,
        "second_leg_with_atr": second_leg_with_atr,
        "final_bar_with_atr": final_bar_with,
        "pullback_holds_impulse_mid": pullback_holds_impulse_mid,
        "trend_boundary": trend_boundary,
        "trend_probe_extreme": trend_probe_extreme,
        "probe_extension_atr": probe_extension_atr,
        "probe_close_retention": probe_close_retention,
        "first_crack_atr": first_crack_atr,
        "crack_inside_atr": crack_inside_atr,
        "reentry_extreme": reentry_extreme,
        "reentry_reach_atr": reentry_reach_atr,
        "reentry_acceptance_atr": reentry_acceptance_atr,
        "failure_resume_atr": failure_resume_atr,
        "failure_progress_from_reentry_atr": failure_progress_from_reentry_atr,
        "interaction_opening_flow_alignment": impulse_side * opening_flow,
        "interaction_flow_alignment": impulse_side * interaction_flow,
        "oi_change_15m": oi_change,
        "interaction_premium_z": premium_z,
        "participation": participation,
    }


def _confirmation_context(
    *, side: int, confirmation_feature: FeatureObservation
) -> dict[str, float | int | bool | str]:
    flow = _finite(confirmation_feature.flow_60s, 0.0)
    opening = _finite(confirmation_feature.flow_open_10s, 0.0)
    return {
        "confirmation_observed_time_ns": int(confirmation_feature.observed_time_ns),
        "confirmation_flow_alignment": side * flow,
        "confirmation_opening_flow_alignment": side * opening,
        "confirmation_efficiency": _finite(confirmation_feature.efficiency_60s, 0.0),
        "confirmation_premium_z": _finite(confirmation_feature.premium_z, 0.0),
    }


def _geometry(
    *,
    side: int,
    entry: float,
    raw_stop: float,
    raw_objective: float,
    atr: float,
    config: RouteConfig,
) -> tuple[float, float, float] | None:
    raw_distance = side * (entry - raw_stop)
    if not math.isfinite(raw_distance) or raw_distance <= 0.0:
        return None
    if raw_distance > config.max_stop_atr * atr:
        return None
    distance = max(raw_distance, config.min_stop_atr * atr)
    stop = entry - side * (distance + config.stop_buffer_atr * atr)
    reward = side * (raw_objective - entry)
    risk = side * (entry - stop)
    if not (
        math.isfinite(reward)
        and math.isfinite(risk)
        and reward > 0.0
        and risk > 0.0
    ):
        return None
    rr = reward / risk
    if rr < config.min_geometry_r:
        return None
    return stop, raw_objective, rr


def _decision(
    *,
    symbol: str,
    state: str,
    side: int,
    score: float,
    target_r: float,
    context: Mapping[str, float | int | bool | str],
    entry: float,
    raw_stop: float,
    raw_objective: float,
    reasons: tuple[str, ...],
    config: RouteConfig,
) -> RouteDecision:
    atr = float(context["atr"])
    geometry = _geometry(
        side=side,
        entry=entry,
        raw_stop=raw_stop,
        raw_objective=raw_objective,
        atr=atr,
        config=config,
    )
    if geometry is None:
        diagnostics = dict(context)
        diagnostics["entry"] = entry
        return _unresolved(symbol, "INVALID_STRUCTURAL_GEOMETRY", diagnostics)
    stop, objective, rr = geometry
    if rr + 1e-12 < target_r:
        diagnostics = dict(context)
        diagnostics.update(
            {
                "entry": entry,
                "geometry_rr": rr,
                "policy_target_r_floor": target_r,
            }
        )
        return _unresolved(symbol, "POLICY_REWARD_SPACE_NOT_MET", diagnostics)
    diagnostics = dict(context)
    diagnostics.update(
        {
            "entry": entry,
            "geometry_rr": rr,
            "policy_target_r_floor": target_r,
            "entry_policy": "PASSIVE_RETEST_LIMIT",
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=state,
        side=side,
        score=score + 0.25 * _clamp(rr / max(target_r, _EPS), 0.0, 2.0),
        expected_target_r=rr,
        atr=atr,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=int(context["episode_ts"]),
        reasons=reasons,
        diagnostics=diagnostics,
    )


def classify_symbol(
    *,
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    peer_breadth: float,
    leader_side: int,
    leader_strength_atr: float,
    leader_symbol: str = "",
    median_same_side_strength_atr: float = 0.0,
    config: RouteConfig = RouteConfig(),
    confirmation_feature: FeatureObservation | None = None,
) -> RouteDecision:
    """Classify a symbol after separating interaction and confirmation data."""
    try:
        context = _extract_context(bars, feature, config)
    except ValueError as exc:
        return _unresolved(symbol, str(exc))
    if not feature.ready:
        return _unresolved(symbol, "INTERACTION_FEATURE_NOT_READY", context)
    confirmation = confirmation_feature or feature
    if not confirmation.ready:
        return _unresolved(symbol, "CONFIRMATION_FEATURE_NOT_READY", context)

    impulse_side = int(context["impulse_side"])
    if impulse_side == 0:
        return _unresolved(symbol, "NO_DIRECTIONAL_EXPANSION", context)
    context.update(
        _confirmation_context(side=impulse_side, confirmation_feature=confirmation)
    )
    context.update(
        {
            "leader_symbol": leader_symbol,
            "leader_side": leader_side,
            "leader_strength_atr": leader_strength_atr,
            "median_same_side_strength_atr": median_same_side_strength_atr,
            "peer_breadth": peer_breadth,
            "leader_excess_atr": leader_strength_atr - median_same_side_strength_atr,
        }
    )

    oi_change = float(context["oi_change_15m"])
    if not math.isfinite(oi_change):
        return _unresolved(symbol, "OPEN_INTEREST_UNAVAILABLE", context)
    if abs(float(context["confirmation_premium_z"])) > config.max_abs_premium_z:
        return _unresolved(symbol, "EXTREME_PREMIUM_CROWDING", context)

    atr = float(context["atr"])
    participation = float(context["participation"])
    context_range_atr = float(context["context_range_atr"])
    range_ok = (
        config.min_context_range_atr
        <= context_range_atr
        <= config.max_context_range_atr
    )

    # Family 1: actual cross-asset leader, fresh expansion, first shallow
    # pullback, then renewed initiative.  A laggard is intentionally ineligible.
    leader_excess = leader_strength_atr - median_same_side_strength_atr
    is_actual_leader = (
        symbol == leader_symbol
        and impulse_side == leader_side
        and leader_strength_atr >= config.min_impulse_atr_continuation
        and leader_excess >= config.min_leader_excess_atr
    )
    fresh_break = (
        config.min_break_close_atr
        <= float(context["break_close_atr"])
        <= config.max_fresh_break_extension_atr
    )
    shallow_turn = (
        config.min_pullback_fraction
        <= float(context["pullback_fraction"])
        <= config.max_pullback_fraction
        and bool(context["pullback_holds_impulse_mid"])
        and float(context["first_leg_against_atr"])
        >= config.min_first_leg_against_atr
        and float(context["second_leg_with_atr"])
        >= config.min_reacceleration_atr
        and float(context["final_bar_with_atr"])
        >= 0.5 * config.min_reacceleration_atr
        and float(context["response_retention_atr"]) >= -0.05
    )
    leader_continuation = (
        range_ok
        and is_actual_leader
        and peer_breadth >= config.min_peer_breadth_fraction
        and float(context["impulse_close_location"])
        >= config.min_impulse_close_location
        and float(context["impulse_path_efficiency"])
        >= config.min_impulse_path_efficiency
        and fresh_break
        and shallow_turn
        and participation >= config.min_participation_ratio
        and oi_change >= config.min_oi_change_for_leader
        and float(context["confirmation_flow_alignment"])
        >= config.min_confirmation_flow
    )
    leader_score = (
        0.75 * _clamp(
            leader_strength_atr / max(config.min_impulse_atr_continuation, _EPS),
            0.0,
            2.0,
        )
        + 0.80 * _clamp(
            leader_excess / max(config.min_leader_excess_atr, _EPS), 0.0, 2.0
        )
        + 0.55 * _clamp(
            float(context["impulse_close_location"])
            / max(config.min_impulse_close_location, _EPS),
            0.0,
            2.0,
        )
        + 0.55 * _clamp(
            float(context["impulse_path_efficiency"])
            / max(config.min_impulse_path_efficiency, _EPS),
            0.0,
            2.0,
        )
        + 0.55 * _clamp(
            float(context["second_leg_with_atr"])
            / max(config.min_reacceleration_atr, _EPS),
            0.0,
            2.0,
        )
        + 0.70 * _clamp(
            float(context["confirmation_flow_alignment"])
            / max(config.min_confirmation_flow, _EPS),
            0.0,
            2.0,
        )
        + 0.35 * _clamp(
            participation / max(config.min_participation_ratio, _EPS), 0.0, 2.0
        )
        + 0.30 * _clamp(
            peer_breadth / max(config.min_peer_breadth_fraction, _EPS), 0.0, 2.0
        )
    )

    # Family 2: mature trend, probe, crack, failed re-entry, resumed failure.
    # This sequence is explicitly more demanding than a wick/effort fade.
    trend_side = int(context["context_side"])
    context_mature = (
        trend_side != 0
        and impulse_side == trend_side
        and abs(float(context["context_trend_atr"]))
        >= config.min_mature_context_trend_atr
        and context_range_atr >= config.min_mature_context_range_atr
        and float(context["context_close_location"])
        >= config.min_mature_close_location
    )
    failed_reentry_sequence = (
        float(context["probe_extension_atr"]) >= config.min_probe_extension_atr
        and float(context["probe_close_retention"])
        <= config.max_probe_close_retention
        and float(context["first_crack_atr"]) >= config.min_first_crack_atr
        and float(context["crack_inside_atr"]) >= config.min_crack_inside_atr
        and float(context["reentry_reach_atr"])
        >= -config.reentry_reach_tolerance_atr
        and float(context["reentry_acceptance_atr"])
        <= config.max_reentry_acceptance_atr
        and float(context["failure_resume_atr"])
        >= config.min_failure_resume_atr
        and float(context["failure_progress_from_reentry_atr"])
        >= config.min_failure_resume_atr
    )
    confirmation_against_trend = -float(context["confirmation_flow_alignment"])
    mature_failure = (
        range_ok
        and context_mature
        and failed_reentry_sequence
        and participation >= config.min_participation_ratio
        and oi_change <= config.max_oi_build_on_failed_reentry
        and confirmation_against_trend >= config.min_reversal_flow
    )
    failure_score = (
        0.60 * _clamp(
            abs(float(context["context_trend_atr"]))
            / max(config.min_mature_context_trend_atr, _EPS),
            0.0,
            2.0,
        )
        + 0.55 * _clamp(
            float(context["probe_extension_atr"])
            / max(config.min_probe_extension_atr, _EPS),
            0.0,
            2.0,
        )
        + 0.75 * _clamp(
            float(context["first_crack_atr"])
            / max(config.min_first_crack_atr, _EPS),
            0.0,
            2.0,
        )
        + 0.60 * _clamp(
            (config.max_reentry_acceptance_atr
             - float(context["reentry_acceptance_atr"]) + 0.05)
            / 0.08,
            0.0,
            2.0,
        )
        + 0.75 * _clamp(
            float(context["failure_progress_from_reentry_atr"])
            / max(config.min_failure_resume_atr, _EPS),
            0.0,
            2.0,
        )
        + 0.80 * _clamp(
            confirmation_against_trend / max(config.min_reversal_flow, _EPS),
            0.0,
            2.0,
        )
        + 0.30 * _clamp(
            participation / max(config.min_participation_ratio, _EPS), 0.0, 2.0
        )
    )

    eligible: list[tuple[str, float]] = []
    if leader_continuation:
        eligible.append(("LEADER_FIRST_PULLBACK_CONTINUATION", leader_score))
    if mature_failure:
        eligible.append(("MATURE_EXTENSION_FAILED_REENTRY", failure_score))
    eligible.sort(key=lambda item: item[1], reverse=True)

    if not eligible or eligible[0][1] < config.min_route_score:
        if symbol != leader_symbol and shallow_turn and fresh_break:
            return _unresolved(symbol, "LAGGARD_NOT_ACTUAL_LEADER", context)
        if context_mature and float(context["first_crack_atr"]) < config.min_first_crack_atr:
            return _unresolved(symbol, "MATURE_TREND_WITHOUT_FIRST_CRACK", context)
        if context_mature and float(context["first_crack_atr"]) >= config.min_first_crack_atr:
            if float(context["reentry_reach_atr"]) < -config.reentry_reach_tolerance_atr:
                return _unresolved(symbol, "NO_REENTRY_ATTEMPT_AFTER_CRACK", context)
            return _unresolved(symbol, "REENTRY_FAILURE_NOT_CONFIRMED", context)
        if float(context["confirmation_flow_alignment"]) < config.min_confirmation_flow:
            return _unresolved(symbol, "ENTRY_INITIATIVE_NOT_CONFIRMED", context)
        return _unresolved(symbol, "CAUSAL_STATE_NOT_COHERENT", context)
    if (
        len(eligible) > 1
        and eligible[0][1] - eligible[1][1] < config.ambiguity_score_gap
    ):
        return _unresolved(symbol, "INSTRUMENT_STATE_AMBIGUITY", context)

    state, score = eligible[0]
    if state == "LEADER_FIRST_PULLBACK_CONTINUATION":
        side = impulse_side
        confirmation_bar = bars[-1]
        # The confirmation bar open is a price-controlled first retest.  It is
        # behind the current close whenever renewed initiative is genuine.
        entry = float(confirmation_bar.open)
        raw_stop = (
            float(context["response_low"])
            if side > 0
            else float(context["response_high"])
        )
        extension = min(
            config.continuation_extension_fraction * float(context["impulse_range"]),
            config.continuation_extension_cap_atr * atr,
        )
        raw_objective = float(context["impulse_extreme"]) + side * extension
        return _decision(
            symbol=symbol,
            state=state,
            side=side,
            score=score,
            target_r=config.continuation_target_r,
            context=context,
            entry=entry,
            raw_stop=raw_stop,
            raw_objective=raw_objective,
            reasons=(
                "FRESH_CROSS_ASSET_LEADER",
                "PARTICIPATION_EXPANSION",
                "FIRST_SHALLOW_PULLBACK",
                "PULLBACK_HELD_IMPULSE_MID",
                "RENEWED_INITIATIVE_CONFIRMED",
                "PASSIVE_CONFIRMATION_RETEST",
            ),
            config=config,
        )

    trade_side = -trend_side
    boundary = float(context["trend_boundary"])
    raw_stop = float(context["trend_probe_extreme"])
    raw_objective = float(context["context_mid"])
    return _decision(
        symbol=symbol,
        state=state,
        side=trade_side,
        score=score,
        target_r=config.reversal_target_r,
        context=context,
        entry=boundary,
        raw_stop=raw_stop,
        raw_objective=raw_objective,
        reasons=(
            "MATURE_ONE_HOUR_EXTENSION",
            "EXTERNAL_PROBE_WITH_WEAK_RETENTION",
            "FIRST_CRACK_BACK_INTO_VALUE",
            "REENTRY_ATTEMPT_FAILED",
            "OPPOSITE_INITIATIVE_RESUMED",
            "PASSIVE_FAILED_BOUNDARY_RETEST",
        ),
        config=config,
    )


def _raw_contexts(
    *,
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig,
) -> dict[str, Mapping[str, float | int | bool | str]]:
    raw: dict[str, Mapping[str, float | int | bool | str]] = {}
    for symbol, bars in bars_by_symbol.items():
        feature = features_by_symbol.get(symbol, FeatureObservation(0, ready=False))
        try:
            raw[symbol] = _extract_context(bars, feature, config)
        except ValueError:
            continue
    return raw


def _leader_state(
    contexts: Mapping[str, Mapping[str, float | int | bool | str]],
) -> tuple[str, int, float, float]:
    candidates: list[tuple[float, str, int]] = []
    for symbol, context in contexts.items():
        side = int(context.get("impulse_side", 0))
        strength = float(context.get("impulse_atr", 0.0))
        if side and strength > 0.0:
            candidates.append((strength, symbol, side))
    if not candidates:
        return "", 0, 0.0, 0.0
    candidates.sort(key=lambda item: (item[0], item[1] == "BTCUSDT", item[1]), reverse=True)
    strength, symbol, side = candidates[0]
    same_side = [item[0] for item in candidates if item[2] == side]
    same_median = median(same_side) if same_side else 0.0
    return symbol, side, strength, same_median


def route_universe(
    *,
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
    confirmation_features_by_symbol: Mapping[str, FeatureObservation] | None = None,
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    symbols = tuple(sorted(bars_by_symbol))
    if not symbols:
        return None, {}

    raw = _raw_contexts(
        bars_by_symbol=bars_by_symbol,
        features_by_symbol=features_by_symbol,
        config=config,
    )
    leader_symbol, leader_side, leader_strength, median_same = _leader_state(raw)
    directional = [
        int(context.get("impulse_side", 0))
        for context in raw.values()
        if int(context.get("impulse_side", 0)) != 0
    ]
    confirmations = confirmation_features_by_symbol or features_by_symbol

    decisions: dict[str, RouteDecision] = {}
    for symbol in symbols:
        local_side = int(raw.get(symbol, {}).get("impulse_side", 0))
        same = sum(1 for side in directional if local_side and side == local_side)
        breadth = same / len(directional) if directional and local_side else 0.0
        decisions[symbol] = classify_symbol(
            symbol=symbol,
            bars=bars_by_symbol[symbol],
            feature=features_by_symbol.get(symbol, FeatureObservation(0, ready=False)),
            confirmation_feature=confirmations.get(symbol, FeatureObservation(0, ready=False)),
            peer_breadth=breadth,
            leader_side=leader_side,
            leader_strength_atr=leader_strength,
            leader_symbol=leader_symbol,
            median_same_side_strength_atr=median_same,
            config=config,
        )

    actionable = [decision for decision in decisions.values() if decision.actionable]
    if not actionable:
        return None, decisions
    actionable.sort(
        key=lambda decision: (
            decision.score,
            decision.expected_target_r,
            decision.symbol == leader_symbol,
            decision.symbol == "BTCUSDT",
            decision.symbol,
        ),
        reverse=True,
    )
    winner = actionable[0]
    conflicting = [item for item in actionable[1:] if item.side != winner.side]
    if conflicting and winner.score - conflicting[0].score < config.ambiguity_score_gap:
        return None, decisions
    return winner, decisions
