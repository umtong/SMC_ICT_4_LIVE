"""Pure causal state router for Candidate 35.

The router has no execution or accounting code. It converts completed one-minute
observations from a single quarter-hour auction episode into one of three states:
continuation, exhaustion reversal, or unresolved. Cross-sectional breadth is
computed before any symbol is ranked, so the final choice is one coherent policy
rather than four independent strategies whose trades are added after the fact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from statistics import median
from typing import Mapping, Sequence


_EPS = 1e-12


def _finite(value: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _sign(value: float, deadband: float = 0.0) -> int:
    if value > deadband:
        return 1
    if value < -deadband:
        return -1
    return 0


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


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
    min_impulse_atr_continuation: float = 0.75
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.12
    min_break_acceptance_atr: float = 0.015
    min_sweep_penetration_atr: float = 0.06
    min_participation_ratio: float = 1.05
    min_flow_alignment: float = 0.04
    min_efficiency: float = 0.28
    min_breadth_fraction: float = 0.50
    continuation_target_r: float = 2.20
    reversal_target_r: float = 1.80
    min_route_score: float = 3.10
    ambiguity_score_gap: float = 0.20
    stop_buffer_atr: float = 0.10
    min_stop_atr: float = 0.42
    max_stop_atr: float = 2.20


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
    prior: Sequence[BarObservation],
    response: Sequence[BarObservation],
) -> float:
    baseline = [bar.volume for bar in prior if math.isfinite(bar.volume) and bar.volume > 0.0]
    current = [bar.volume for bar in response if math.isfinite(bar.volume) and bar.volume > 0.0]
    if not baseline or not current:
        return 0.0
    return (sum(current) / len(current)) / max(median(baseline), _EPS)


def _effort_result(response: Sequence[BarObservation]) -> float:
    path = sum(max(bar.high - bar.low, 0.0) for bar in response)
    progress = abs(response[-1].close - response[0].open)
    return progress / max(path, _EPS)


def _raw_context(
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig,
) -> dict[str, float | int | bool]:
    required = config.atr_period + config.prior_bars + config.response_bars + 1
    if len(bars) < required:
        raise ValueError(f"need at least {required} completed bars, got {len(bars)}")
    response = list(bars[-config.response_bars :])
    prior = list(bars[-(config.prior_bars + config.response_bars) : -config.response_bars])
    atr = causal_atr(bars[: -config.response_bars + 1], config.atr_period)
    if not math.isfinite(atr) or atr <= 0.0:
        raise ValueError("causal ATR is unavailable")

    prior_open = prior[0].open
    prior_close = prior[-1].close
    prior_high = max(bar.high for bar in prior)
    prior_low = min(bar.low for bar in prior)
    response_open = response[0].open
    response_close = response[-1].close
    response_high = max(bar.high for bar in response)
    response_low = min(bar.low for bar in response)

    impulse_raw = prior_close - prior_open
    impulse_side = _sign(impulse_raw, deadband=0.02 * atr)
    impulse_atr = abs(impulse_raw) / atr
    response_signed_atr = (
        impulse_side * (response_close - response_open) / atr
        if impulse_side
        else 0.0
    )
    prior_extreme = prior_high if impulse_side > 0 else prior_low
    response_extreme = response_high if impulse_side > 0 else response_low
    breakout_close_atr = (
        impulse_side * (response_close - prior_extreme) / atr
        if impulse_side
        else 0.0
    )
    penetration_atr = (
        impulse_side * (response_extreme - prior_extreme) / atr
        if impulse_side
        else 0.0
    )
    sweep_failed = (
        impulse_side != 0
        and penetration_atr >= config.min_sweep_penetration_atr
        and breakout_close_atr < 0.0
    )

    opening_flow = _finite(feature.flow_open_10s)
    tail_flow = _finite(feature.flow_60s)
    efficiency = _finite(feature.efficiency_60s, _effort_result(response))
    opening_alignment = impulse_side * opening_flow
    tail_alignment = impulse_side * tail_flow
    participation = max(
        _finite(feature.notional_open_10s_burst),
        _volume_ratio(prior, response),
    )
    crowd_alignment = impulse_side * _finite(feature.premium_z)
    oi_alignment = impulse_side * _finite(feature.oi_change_15m)

    return {
        "atr": atr,
        "impulse_side": impulse_side,
        "impulse_atr": impulse_atr,
        "response_signed_atr": response_signed_atr,
        "breakout_close_atr": breakout_close_atr,
        "penetration_atr": penetration_atr,
        "sweep_failed": sweep_failed,
        "opening_alignment": opening_alignment,
        "tail_alignment": tail_alignment,
        "participation": participation,
        "efficiency": efficiency,
        "crowd_alignment": crowd_alignment,
        "oi_alignment": oi_alignment,
        "prior_open": prior_open,
        "prior_close": prior_close,
        "prior_high": prior_high,
        "prior_low": prior_low,
        "response_open": response_open,
        "response_close": response_close,
        "response_high": response_high,
        "response_low": response_low,
        "episode_ts": response[-1].ts_event,
    }


def _unresolved(symbol: str, context: Mapping[str, float | int | bool], reason: str) -> RouteDecision:
    return RouteDecision(
        symbol=symbol,
        state="UNRESOLVED",
        side=0,
        score=0.0,
        expected_target_r=0.0,
        atr=_finite(context.get("atr", math.nan)),
        entry_reference=_finite(context.get("response_close", math.nan), math.nan),
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(context.get("episode_ts", 0)),
        reasons=(reason,),
        diagnostics=dict(context),
    )


def classify_symbol(
    *,
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    breadth_fraction: float,
    btc_impulse_side: int,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    """Classify one symbol after the same completed three-minute response window."""
    try:
        context = _raw_context(bars, feature, config)
    except ValueError as exc:
        return _unresolved(symbol, {}, str(exc))
    if not feature.ready:
        return _unresolved(symbol, context, "FEATURE_NOT_READY")

    side = int(context["impulse_side"])
    if side == 0:
        return _unresolved(symbol, context, "NO_PRIOR_AUCTION_IMPULSE")

    impulse = float(context["impulse_atr"])
    response = float(context["response_signed_atr"])
    breakout = float(context["breakout_close_atr"])
    participation = float(context["participation"])
    opening_alignment = float(context["opening_alignment"])
    tail_alignment = float(context["tail_alignment"])
    efficiency = float(context["efficiency"])
    sweep_failed = bool(context["sweep_failed"])
    crowd_alignment = float(context["crowd_alignment"])
    oi_alignment = float(context["oi_alignment"])
    breadth_ok = breadth_fraction >= config.min_breadth_fraction
    btc_support = btc_impulse_side == side or symbol == "BTCUSDT"

    accepted = (
        breakout >= config.min_break_acceptance_atr
        or (
            response >= config.min_response_atr
            and tail_alignment >= config.min_flow_alignment
            and efficiency >= config.min_efficiency
        )
    )
    continuation_eligible = (
        impulse >= config.min_impulse_atr_continuation
        and response >= config.min_response_atr
        and participation >= config.min_participation_ratio
        and accepted
        and (breadth_ok or btc_support)
    )
    continuation_score = (
        _clamp(impulse / config.min_impulse_atr_continuation, 0.0, 2.0)
        + 0.90 * _clamp(response / config.min_response_atr, 0.0, 2.0)
        + 0.70 * _clamp(max(breakout, 0.0) / max(config.min_break_acceptance_atr, _EPS), 0.0, 2.0)
        + 0.55 * _clamp(participation / config.min_participation_ratio, 0.0, 2.0)
        + 0.45 * _clamp(max(tail_alignment, opening_alignment, 0.0) / max(config.min_flow_alignment, _EPS), 0.0, 2.0)
        + 0.45 * _clamp(breadth_fraction / max(config.min_breadth_fraction, _EPS), 0.0, 2.0)
        + 0.15 * _clamp(max(oi_alignment, 0.0) / 0.0025, 0.0, 2.0)
        - 0.20 * _clamp(max(crowd_alignment, 0.0) / 2.0, 0.0, 2.0)
    )

    reversal_response = -response
    absorption = (
        sweep_failed
        or breakout <= -0.03
        or (
            opening_alignment >= config.min_flow_alignment
            and reversal_response >= config.min_response_atr
            and efficiency <= max(config.min_efficiency, 0.40)
        )
    )
    reversal_eligible = (
        impulse >= config.min_impulse_atr_reversal
        and reversal_response >= config.min_response_atr
        and participation >= config.min_participation_ratio
        and absorption
    )
    reversal_score = (
        _clamp(impulse / config.min_impulse_atr_reversal, 0.0, 2.0)
        + 0.95 * _clamp(reversal_response / config.min_response_atr, 0.0, 2.0)
        + 0.85 * (1.0 if sweep_failed else _clamp(max(-breakout, 0.0) / 0.05, 0.0, 2.0))
        + 0.55 * _clamp(participation / config.min_participation_ratio, 0.0, 2.0)
        + 0.35 * _clamp(max(opening_alignment, 0.0) / max(config.min_flow_alignment, _EPS), 0.0, 2.0)
        + 0.20 * _clamp(max(crowd_alignment, 0.0) / 1.5, 0.0, 2.0)
        + 0.15 * _clamp(max(-oi_alignment, 0.0) / 0.0025, 0.0, 2.0)
    )

    if continuation_eligible and reversal_eligible:
        if abs(continuation_score - reversal_score) < config.ambiguity_score_gap:
            return _unresolved(symbol, context, "CONTINUATION_REVERSAL_AMBIGUITY")
    if continuation_eligible and continuation_score >= max(config.min_route_score, reversal_score):
        trade_side = side
        state = "PHASE_ACCEPTED_CONTINUATION"
        score = continuation_score
        target_r = config.continuation_target_r
        stop_anchor = (
            min(float(context["response_low"]), float(context["prior_high"]))
            if trade_side > 0
            else max(float(context["response_high"]), float(context["prior_low"]))
        )
        objective = float(context["response_close"]) + trade_side * target_r * max(
            abs(float(context["response_close"]) - stop_anchor),
            config.min_stop_atr * float(context["atr"]),
        )
        reasons = (
            "PRIOR_IMPULSE",
            "BOUNDARY_RESPONSE_ACCEPTED",
            "PARTICIPATION_CONFIRMED",
            "CROSS_ASSET_SUPPORT",
        )
    elif reversal_eligible and reversal_score >= max(config.min_route_score, continuation_score):
        trade_side = -side
        state = "PHASE_EXHAUSTION_REVERSAL"
        score = reversal_score
        target_r = config.reversal_target_r
        stop_anchor = (
            float(context["response_high"])
            if trade_side < 0
            else float(context["response_low"])
        )
        objective = (
            (float(context["prior_open"]) + float(context["prior_close"])) / 2.0
        )
        reasons = (
            "LARGE_PRIOR_IMPULSE",
            "BOUNDARY_ACCEPTANCE_FAILED",
            "OPPOSITE_RESPONSE_CONFIRMED",
        )
    else:
        return _unresolved(symbol, context, "STATE_THRESHOLDS_NOT_COHERENT")

    atr = float(context["atr"])
    entry = float(context["response_close"])
    raw_stop_distance = trade_side * (entry - stop_anchor)
    stop_distance = _clamp(raw_stop_distance, config.min_stop_atr * atr, config.max_stop_atr * atr)
    stop = entry - trade_side * stop_distance - trade_side * config.stop_buffer_atr * atr
    if state == "PHASE_EXHAUSTION_REVERSAL":
        objective_distance = trade_side * (objective - entry)
        minimum = target_r * abs(entry - stop)
        if objective_distance < 1.15 * abs(entry - stop):
            objective = entry + trade_side * minimum
        else:
            objective = entry + trade_side * min(objective_distance, 3.0 * abs(entry - stop))

    diagnostics = dict(context)
    diagnostics.update(
        {
            "breadth_fraction": breadth_fraction,
            "btc_impulse_side": btc_impulse_side,
            "continuation_score": continuation_score,
            "reversal_score": reversal_score,
            "continuation_eligible": continuation_eligible,
            "reversal_eligible": reversal_eligible,
        },
    )
    return RouteDecision(
        symbol=symbol,
        state=state,
        side=trade_side,
        score=score,
        expected_target_r=target_r,
        atr=atr,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=int(context["episode_ts"]),
        reasons=reasons,
        diagnostics=diagnostics,
    )


def route_universe(
    *,
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    """Classify all symbols, then select one actual account decision."""
    contexts: dict[str, dict[str, float | int | bool]] = {}
    for symbol, bars in bars_by_symbol.items():
        feature = features_by_symbol.get(symbol, FeatureObservation(0, ready=False))
        try:
            contexts[symbol] = _raw_context(bars, feature, config)
        except ValueError:
            contexts[symbol] = {"impulse_side": 0}
    sides = [int(item.get("impulse_side", 0)) for item in contexts.values()]
    btc_side = int(contexts.get("BTCUSDT", {}).get("impulse_side", 0))

    decisions: dict[str, RouteDecision] = {}
    for symbol, bars in bars_by_symbol.items():
        side = int(contexts.get(symbol, {}).get("impulse_side", 0))
        aligned = sum(other == side for other in sides if other != 0) if side else 0
        available = sum(other != 0 for other in sides)
        breadth = aligned / available if available else 0.0
        decisions[symbol] = classify_symbol(
            symbol=symbol,
            bars=bars,
            feature=features_by_symbol.get(symbol, FeatureObservation(0, ready=False)),
            breadth_fraction=breadth,
            btc_impulse_side=btc_side,
            config=config,
        )

    actionable = [decision for decision in decisions.values() if decision.actionable]
    if not actionable:
        return None, decisions
    actionable.sort(
        key=lambda item: (
            item.score * item.expected_target_r,
            item.score,
            1 if item.symbol == "BTCUSDT" else 0,
        ),
        reverse=True,
    )
    winner = actionable[0]
    if len(actionable) > 1:
        first = winner.score * winner.expected_target_r
        second = actionable[1].score * actionable[1].expected_target_r
        if first - second < config.ambiguity_score_gap:
            return None, decisions
    return winner, decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "causal_atr",
    "classify_symbol",
    "route_universe",
]
