"""Causal auction-state router for Candidate 39 V2.

The router separates *event evidence* from *entry confirmation*.

- ``feature`` is frozen at the first completed response minute and is used to
  describe the interaction (OI change, participation, initial aggressor flow).
- ``confirmation_feature`` is observed at the third completed response minute
  and is used only to decide whether initiative still agrees at entry time.

This prevents the same observation from defining the state and confirming its
own entry. Actionable decisions use a passive retest reference at the completed
range boundary; the Nautilus strategy decides whether cost-after target space is
large enough and submits a price-controlled limit parent.
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
    atr_period: int = 30
    prior_bars: int = 15
    response_bars: int = 3
    min_impulse_atr_continuation: float = 0.70
    min_impulse_atr_reversal: float = 0.95
    min_response_atr: float = 0.10
    min_participation_ratio: float = 1.05
    min_route_score: float = 4.20
    ambiguity_score_gap: float = 0.35
    continuation_target_r: float = 2.20
    reversal_target_r: float = 1.60
    context_bars: int = 60
    min_context_range_atr: float = 1.20
    max_context_range_atr: float = 7.50
    min_break_atr: float = 0.06
    max_break_extension_atr: float = 1.80
    # At least half of the excursion beyond the boundary must remain at the
    # completed impulse close. This is an auction-coherence rule, not a fitted
    # profit threshold: a wick that gives back most of its excursion is a failed
    # attack, not accepted value.
    min_break_retention: float = 0.50
    boundary_hold_tolerance_atr: float = 0.10
    min_sweep_atr: float = 0.08
    min_opposite_initiative_atr: float = 0.10
    min_flow_alignment: float = 0.035
    min_flow_flip: float = 0.035
    min_oi_build: float = 0.0015
    min_oi_contraction: float = 0.0015
    max_abs_premium_z: float = 3.50
    min_breadth_fraction: float = 0.50
    min_peer_lead_atr: float = 0.22
    max_lag_exhaustion_atr: float = 0.85
    min_geometry_r: float = 1.20
    stop_buffer_atr: float = 0.08
    min_stop_atr: float = 0.32
    max_stop_atr: float = 2.80


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
    context: Sequence[BarObservation],
    event: Sequence[BarObservation],
) -> float:
    baseline = [bar.volume for bar in context if math.isfinite(bar.volume) and bar.volume > 0.0]
    current = [bar.volume for bar in event if math.isfinite(bar.volume) and bar.volume > 0.0]
    if not baseline or not current:
        return 0.0
    return sum(current) / len(current) / max(median(baseline), _EPS)


def _path_efficiency(bars: Sequence[BarObservation]) -> float:
    if not bars:
        return 0.0
    path = sum(max(bar.high - bar.low, 0.0) for bar in bars)
    return abs(bars[-1].close - bars[0].open) / max(path, _EPS)


def _unresolved(
    symbol: str,
    reason: str,
    context: Mapping[str, float | int | bool | str] | None = None,
) -> RouteDecision:
    data = dict(context or {})
    return RouteDecision(
        symbol=symbol,
        state="UNRESOLVED",
        side=0,
        score=0.0,
        expected_target_r=0.0,
        atr=_finite(data.get("atr", math.nan)),
        entry_reference=_finite(data.get("entry", math.nan)),
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(data.get("episode_ts", 0)),
        reasons=(reason,),
        diagnostics=data,
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

    impulse_open = impulse[0].open
    impulse_close = impulse[-1].close
    impulse_high = max(bar.high for bar in impulse)
    impulse_low = min(bar.low for bar in impulse)
    impulse_change = impulse_close - impulse_open
    impulse_side = _sign(impulse_change, 0.03 * atr)
    impulse_atr = abs(impulse_change) / atr

    response_open = response[0].open
    response_close = response[-1].close
    response_high = max(bar.high for bar in response)
    response_low = min(bar.low for bar in response)

    boundary = context_high if impulse_side > 0 else context_low
    impulse_extreme = impulse_high if impulse_side > 0 else impulse_low
    response_extreme = response_high if impulse_side > 0 else response_low
    opposite_extreme = response_low if impulse_side > 0 else response_high

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
    response_close_from_boundary_atr = (
        impulse_side * (response_close - boundary) / atr if impulse_side else 0.0
    )
    response_progress_atr = (
        impulse_side * (response_close - response_open) / atr if impulse_side else 0.0
    )

    first = response[0]
    later = response[1:]
    first_reclaim = (
        impulse_side * (first.close - boundary) < 0.0 if impulse_side else False
    )
    later_opposite_progress = (
        -impulse_side * (later[-1].close - first.close) / atr
        if impulse_side and later
        else 0.0
    )
    later_directional_bars = (
        sum(1 for bar in later if -impulse_side * (bar.close - bar.open) > 0.0)
        if impulse_side
        else 0
    )
    distinct_opposite_initiative = (
        first_reclaim
        and later_opposite_progress >= config.min_opposite_initiative_atr
        and later_directional_bars >= 1
    )

    opening_flow = _finite(feature.flow_open_10s, 0.0)
    interaction_tail_flow = _finite(feature.flow_60s, 0.0)
    opening_with_impulse = impulse_side * opening_flow if impulse_side else 0.0
    interaction_tail_with_impulse = (
        impulse_side * interaction_tail_flow if impulse_side else 0.0
    )
    interaction_tail_against_impulse = -interaction_tail_with_impulse

    oi_change = _finite(feature.oi_change_15m)
    premium_z = _finite(feature.premium_z, 0.0)
    participation = max(
        _finite(feature.notional_open_10s_burst, 0.0),
        _volume_ratio(context[-min(30, len(context)) :], impulse + response),
    )
    interaction_efficiency = _finite(
        feature.efficiency_60s,
        _path_efficiency(response[:1]),
    )

    half = max(2, len(context) // 2)
    context_trend = (context[-1].close - context[-half].open) / atr
    context_trend_side = _sign(context_trend, 0.15)

    return {
        "atr": atr,
        "episode_ts": response[-1].ts_event,
        "entry": boundary,
        "impulse_side": impulse_side,
        "impulse_atr": impulse_atr,
        "context_high": context_high,
        "context_low": context_low,
        "context_mid": context_mid,
        "context_range_atr": context_range / atr,
        "context_trend_atr": context_trend,
        "context_trend_side": context_trend_side,
        "boundary": boundary,
        "impulse_open": impulse_open,
        "impulse_close": impulse_close,
        "impulse_high": impulse_high,
        "impulse_low": impulse_low,
        "impulse_extreme": impulse_extreme,
        "response_open": response_open,
        "response_close": response_close,
        "response_high": response_high,
        "response_low": response_low,
        "response_extreme": response_extreme,
        "opposite_extreme": opposite_extreme,
        "break_close_atr": break_close_atr,
        "break_extreme_atr": break_extreme_atr,
        "break_retention": break_retention,
        "response_close_from_boundary_atr": response_close_from_boundary_atr,
        "response_progress_atr": response_progress_atr,
        "first_reclaim": first_reclaim,
        "later_opposite_progress_atr": later_opposite_progress,
        "later_directional_bars": later_directional_bars,
        "distinct_opposite_initiative": distinct_opposite_initiative,
        "opening_flow_alignment": opening_with_impulse,
        "interaction_tail_flow_alignment": interaction_tail_with_impulse,
        "interaction_opposite_flow_alignment": interaction_tail_against_impulse,
        "oi_change_15m": oi_change,
        "interaction_premium_z": premium_z,
        "participation": participation,
        "interaction_efficiency": interaction_efficiency,
    }


def _confirmation_context(
    *,
    side: int,
    confirmation_feature: FeatureObservation,
) -> dict[str, float | int | bool | str]:
    confirmation_flow = _finite(confirmation_feature.flow_60s, 0.0)
    opening_flow = _finite(confirmation_feature.flow_open_10s, 0.0)
    return {
        "confirmation_observed_time_ns": int(confirmation_feature.observed_time_ns),
        "confirmation_flow_alignment": side * confirmation_flow,
        "confirmation_opening_flow_alignment": side * opening_flow,
        "confirmation_efficiency": _finite(
            confirmation_feature.efficiency_60s,
            0.0,
        ),
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
    # Never pull a structural invalidation inward. A stop farther than the
    # policy maximum invalidates the opportunity; only a too-tight stop may be
    # expanded outward to the minimum operational distance.
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
        data = dict(context)
        data["entry"] = entry
        return _unresolved(symbol, "INVALID_STRUCTURAL_GEOMETRY", data)
    stop, objective, rr = geometry
    if rr + 1e-12 < target_r:
        data = dict(context)
        data.update({"entry": entry, "geometry_rr": rr, "policy_target_r_floor": target_r})
        return _unresolved(symbol, "POLICY_REWARD_SPACE_NOT_MET", data)
    diagnostics = dict(context)
    diagnostics.update(
        {
            "entry": entry,
            "geometry_rr": rr,
            "policy_target_r_floor": target_r,
            "entry_policy": "PASSIVE_BOUNDARY_RETEST_LIMIT",
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=state,
        side=side,
        score=score + 0.3 * _clamp(rr / max(target_r, _EPS), 0.0, 2.0),
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
    config: RouteConfig = RouteConfig(),
    confirmation_feature: FeatureObservation | None = None,
) -> RouteDecision:
    """Classify one symbol using separated interaction and confirmation data."""
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
        return _unresolved(symbol, "NO_DIRECTIONAL_15M_AUCTION", context)

    context.update(
        _confirmation_context(
            side=impulse_side,
            confirmation_feature=confirmation,
        )
    )
    oi_change = float(context["oi_change_15m"])
    if not math.isfinite(oi_change):
        return _unresolved(symbol, "OPEN_INTEREST_UNAVAILABLE", context)
    if abs(float(context["confirmation_premium_z"])) > config.max_abs_premium_z:
        return _unresolved(symbol, "EXTREME_PREMIUM_CROWDING", context)

    impulse = float(context["impulse_atr"])
    break_close = float(context["break_close_atr"])
    break_extreme = float(context["break_extreme_atr"])
    break_retention = float(context["break_retention"])
    response_hold = float(context["response_close_from_boundary_atr"])
    opening_with = float(context["opening_flow_alignment"])
    confirmation_with = float(context["confirmation_flow_alignment"])
    confirmation_against = -confirmation_with
    participation = float(context["participation"])
    context_range = float(context["context_range_atr"])
    trend_side = int(context["context_trend_side"])

    breadth_support = peer_breadth >= config.min_breadth_fraction
    leader_support = (
        leader_side == impulse_side
        and leader_strength_atr >= config.min_peer_lead_atr
    )
    range_ok = config.min_context_range_atr <= context_range <= config.max_context_range_atr
    coherent_break = (
        config.min_break_atr <= break_close <= config.max_break_extension_atr
        and break_extreme > 0.0
        and break_retention >= config.min_break_retention
    )

    build_accept = (
        range_ok
        and impulse >= config.min_impulse_atr_continuation
        and coherent_break
        and response_hold >= -config.boundary_hold_tolerance_atr
        and float(context["response_progress_atr"]) >= -config.min_response_atr
        and oi_change >= config.min_oi_build
        and confirmation_with >= config.min_flow_alignment
        and participation >= config.min_participation_ratio
        and (breadth_support or leader_support or trend_side == impulse_side)
    )
    build_score = (
        0.75 * _clamp(impulse / config.min_impulse_atr_continuation, 0.0, 2.0)
        + 0.80 * _clamp(break_close / max(config.min_break_atr, _EPS), 0.0, 2.0)
        + 0.90 * _clamp(break_retention / max(config.min_break_retention, _EPS), 0.0, 2.0)
        + 0.60 * _clamp(
            (response_hold + config.boundary_hold_tolerance_atr) / 0.2,
            0.0,
            2.0,
        )
        + 0.80 * _clamp(oi_change / max(config.min_oi_build, _EPS), 0.0, 2.0)
        + 0.90 * _clamp(
            confirmation_with / max(config.min_flow_alignment, _EPS),
            0.0,
            2.0,
        )
        + 0.35 * _clamp(
            participation / max(config.min_participation_ratio, _EPS),
            0.0,
            2.0,
        )
        + 0.35 * _clamp(
            peer_breadth / max(config.min_breadth_fraction, _EPS),
            0.0,
            2.0,
        )
        + 0.20 * (1.0 if trend_side == impulse_side else 0.0)
    )

    cascade_reclaim = (
        range_ok
        and impulse >= config.min_impulse_atr_reversal
        and break_extreme >= config.min_sweep_atr
        and response_hold < -config.boundary_hold_tolerance_atr
        and oi_change <= -config.min_oi_contraction
        and opening_with >= config.min_flow_alignment
        and confirmation_against >= config.min_flow_flip
        and bool(context["distinct_opposite_initiative"])
        and participation >= config.min_participation_ratio
    )
    cascade_score = (
        0.75 * _clamp(impulse / config.min_impulse_atr_reversal, 0.0, 2.0)
        + 0.90 * _clamp(break_extreme / max(config.min_sweep_atr, _EPS), 0.0, 2.0)
        + 0.90 * _clamp(
            -response_hold / max(config.boundary_hold_tolerance_atr, _EPS),
            0.0,
            2.0,
        )
        + 0.85 * _clamp(
            -oi_change / max(config.min_oi_contraction, _EPS),
            0.0,
            2.0,
        )
        + 0.50 * _clamp(
            opening_with / max(config.min_flow_alignment, _EPS),
            0.0,
            2.0,
        )
        + 0.90 * _clamp(
            confirmation_against / max(config.min_flow_flip, _EPS),
            0.0,
            2.0,
        )
        + 0.60 * _clamp(
            float(context["later_opposite_progress_atr"])
            / max(config.min_opposite_initiative_atr, _EPS),
            0.0,
            2.0,
        )
        + 0.30 * _clamp(
            participation / max(config.min_participation_ratio, _EPS),
            0.0,
            2.0,
        )
    )

    peer_repricing = (
        symbol != "BTCUSDT"
        and leader_support
        and range_ok
        and impulse >= 0.5 * config.min_impulse_atr_continuation
        and coherent_break
        and break_close <= config.max_lag_exhaustion_atr
        and response_hold >= -0.5 * config.boundary_hold_tolerance_atr
        and oi_change >= config.min_oi_build
        and confirmation_with >= config.min_flow_alignment
        and participation >= config.min_participation_ratio
    )
    peer_score = (
        0.70 * _clamp(
            leader_strength_atr / max(config.min_peer_lead_atr, _EPS),
            0.0,
            2.0,
        )
        + 0.65 * _clamp(break_close / max(config.min_break_atr, _EPS), 0.0, 2.0)
        + 0.75 * _clamp(break_retention / max(config.min_break_retention, _EPS), 0.0, 2.0)
        + 0.80 * _clamp(oi_change / max(config.min_oi_build, _EPS), 0.0, 2.0)
        + 0.90 * _clamp(
            confirmation_with / max(config.min_flow_alignment, _EPS),
            0.0,
            2.0,
        )
        + 0.40 * _clamp(
            participation / max(config.min_participation_ratio, _EPS),
            0.0,
            2.0,
        )
        + 0.45 * _clamp(
            peer_breadth / max(config.min_breadth_fraction, _EPS),
            0.0,
            2.0,
        )
    )

    eligible: list[tuple[str, float]] = []
    if build_accept:
        eligible.append(("BUILD_ACCEPT_CONTINUATION", build_score))
    if cascade_reclaim:
        eligible.append(("CASCADE_RECLAIM_REVERSAL", cascade_score))
    if peer_repricing:
        eligible.append(("PEER_LED_REPRICING", peer_score))
    eligible.sort(key=lambda item: item[1], reverse=True)

    if not eligible or eligible[0][1] < config.min_route_score:
        if not coherent_break and break_extreme > 0.0 and break_close > 0.0:
            return _unresolved(symbol, "FAILED_WICK_NOT_ACCEPTED_VALUE", context)
        if confirmation_with < config.min_flow_alignment and break_close > 0.0:
            return _unresolved(symbol, "ENTRY_INITIATIVE_NOT_CONFIRMED", context)
        return _unresolved(symbol, "CAUSAL_STATE_NOT_COHERENT", context)
    if (
        len(eligible) > 1
        and eligible[0][1] - eligible[1][1] < config.ambiguity_score_gap
    ):
        return _unresolved(symbol, "INSTRUMENT_STATE_AMBIGUITY", context)

    state, score = eligible[0]
    boundary = float(context["boundary"])
    context_range_price = float(context["context_high"]) - float(context["context_low"])

    if state == "CASCADE_RECLAIM_REVERSAL":
        trade_side = -impulse_side
        raw_stop = float(context["impulse_extreme"])
        natural_objective = (
            float(context["context_low"])
            if trade_side < 0
            else float(context["context_high"])
        )
        return _decision(
            symbol=symbol,
            state=state,
            side=trade_side,
            score=score,
            target_r=config.reversal_target_r,
            context=context,
            entry=boundary,
            raw_stop=raw_stop,
            raw_objective=natural_objective,
            reasons=(
                "EXTERNAL_LIQUIDITY_SWEEP",
                "OPEN_INTEREST_CONTRACTION",
                "BOUNDARY_RECLAIM_FROZEN",
                "LATER_OPPOSITE_INITIATIVE",
                "ENTRY_FLOW_CONFIRMED_AFTER_ATTACK",
                "PASSIVE_BOUNDARY_RETEST",
            ),
            config=config,
        )

    trade_side = impulse_side
    atr = float(context["atr"])
    raw_stop = (
        min(
            float(context["response_low"]),
            boundary - config.boundary_hold_tolerance_atr * atr,
        )
        if trade_side > 0
        else max(
            float(context["response_high"]),
            boundary + config.boundary_hold_tolerance_atr * atr,
        )
    )
    natural_objective = boundary + trade_side * context_range_price
    reasons = (
        "RANGE_LIQUIDITY_BOUNDARY_BROKEN",
        "BREAK_EXTREME_RETAINED_AT_CLOSE",
        "OPEN_INTEREST_BUILD",
        "ENTRY_FLOW_CONFIRMED",
        "BOUNDARY_ACCEPTED",
        "PASSIVE_BOUNDARY_RETEST",
    )
    if state == "PEER_LED_REPRICING":
        reasons = (
            "LEADER_DIRECTION_ESTABLISHED",
            "LAGGING_ASSET_BOUNDARY_ACCEPTED",
            "BREAK_EXTREME_RETAINED_AT_CLOSE",
            "OPEN_INTEREST_BUILD",
            "ENTRY_FLOW_CONFIRMED",
            "PASSIVE_BOUNDARY_RETEST",
        )
    return _decision(
        symbol=symbol,
        state=state,
        side=trade_side,
        score=score,
        target_r=config.continuation_target_r,
        context=context,
        entry=boundary,
        raw_stop=raw_stop,
        raw_objective=natural_objective,
        reasons=reasons,
        config=config,
    )


def _leader_state(
    contexts: Mapping[str, Mapping[str, float | int | bool | str]],
) -> tuple[int, float]:
    btc = contexts.get("BTCUSDT")
    if btc is not None:
        side = int(btc.get("impulse_side", 0))
        strength = max(float(btc.get("break_close_atr", 0.0)), 0.0)
        if side and strength > 0.0:
            return side, strength
    signed = [
        int(context.get("impulse_side", 0))
        * max(float(context.get("break_close_atr", 0.0)), 0.0)
        for context in contexts.values()
    ]
    if not signed:
        return 0, 0.0
    middle = median(signed)
    return _sign(middle, 0.05), abs(middle)


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

    raw: dict[str, Mapping[str, float | int | bool | str]] = {}
    for symbol in symbols:
        try:
            raw[symbol] = _extract_context(
                bars_by_symbol[symbol],
                features_by_symbol[symbol],
                config,
            )
        except (KeyError, ValueError):
            continue
    leader_side, leader_strength = _leader_state(raw)
    directional = [
        int(context["impulse_side"])
        for context in raw.values()
        if int(context["impulse_side"]) != 0
    ]

    confirmations = confirmation_features_by_symbol or features_by_symbol
    decisions: dict[str, RouteDecision] = {}
    for symbol in symbols:
        local_side = int(raw.get(symbol, {}).get("impulse_side", 0))
        same = sum(1 for side in directional if local_side and side == local_side)
        breadth = same / len(directional) if directional and local_side else 0.0
        interaction = features_by_symbol.get(
            symbol,
            FeatureObservation(0, ready=False),
        )
        confirmation = confirmations.get(
            symbol,
            FeatureObservation(0, ready=False),
        )
        decisions[symbol] = classify_symbol(
            symbol=symbol,
            bars=bars_by_symbol[symbol],
            feature=interaction,
            confirmation_feature=confirmation,
            peer_breadth=breadth,
            leader_side=leader_side,
            leader_strength_atr=leader_strength,
            config=config,
        )

    actionable = [decision for decision in decisions.values() if decision.actionable]
    if not actionable:
        return None, decisions
    actionable.sort(
        key=lambda decision: (
            decision.score,
            decision.expected_target_r,
            decision.symbol == "BTCUSDT",
            decision.symbol,
        ),
        reverse=True,
    )
    winner = actionable[0]
    opposite = [decision for decision in actionable[1:] if decision.side != winner.side]
    if opposite and winner.score - opposite[0].score < config.ambiguity_score_gap:
        return None, decisions
    return winner, decisions
