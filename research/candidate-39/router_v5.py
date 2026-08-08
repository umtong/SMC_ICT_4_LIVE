"""Candidate 39 V5: feature-informed non-scalping auction router.

V5 keeps the concrete trader-derived price geometry from V4 but repairs two
structural errors exposed by the first seven-day replay:

* a large move is not continuation sponsorship when it is a liquidation/climax;
* a wick back inside a level is not a reversal until positioning and aggressor
  flow transition from the attack to the reclaim.

It also adds an independent Mark-Fisher/ACD-style opening-range acceptance
family to widen opportunity without loosening either existing scenario.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import math
from statistics import median
from typing import Callable, Mapping, Sequence

from router import BarObservation, FeatureObservation, RouteDecision
from router_v4 import (
    SymbolContext,
    TraderDerivedConfig,
    _failed_level_candidate,
    _first_pullback_candidate,
    _make_context,
)

MINUTE_NS = 60_000_000_000
HOUR_NS = 60 * MINUTE_NS
_EPS = 1e-12
FeatureProvider = Callable[[str, int], FeatureObservation]


@dataclass(frozen=True, slots=True)
class InformedRouterConfig:
    price: TraderDerivedConfig = field(default_factory=TraderDerivedConfig)
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.28

    # Sponsored first pullback: initiative, not liquidation/climax.
    min_sponsor_oi_build: float = 0.0010
    min_event_flow_alignment: float = 0.05
    min_confirmation_flow_alignment: float = 0.12
    min_confirmation_efficiency: float = 0.06
    max_confirmation_oi_contraction: float = 0.0005
    max_climax_burst: float = 8.0
    max_initiative_impulse_atr: float = 6.0
    max_side_premium_z: float = 2.75

    # Failed-level reversal must be a real positioning reset.
    min_liquidation_oi_contraction: float = 0.0015
    min_reversal_flow_alignment: float = 0.20
    min_reversal_efficiency: float = 0.08
    min_reaccept_depth_atr: float = 0.12
    min_relative_isolation_atr: float = 0.18
    failed_level_hard_stop_atr: float = 0.70
    failed_level_target_r_floor: float = 1.55

    # 8h session opening-range acceptance/retest.
    opening_range_minutes: int = 60
    min_or_acceptance_atr: float = 0.12
    max_or_acceptance_atr: float = 1.25
    or_event_lookback_bars: int = 4
    or_retest_tolerance_atr: float = 0.20
    min_or_hold_depth_atr: float = 0.06
    min_or_body_fraction: float = 0.42
    min_or_close_location: float = 0.64
    min_or_volume_ratio: float = 1.05
    min_or_breadth_fraction: float = 0.50
    min_or_flow_alignment: float = 0.12
    min_or_sponsor_oi: float = 0.0008
    or_hard_stop_atr: float = 0.55
    or_target_r_floor: float = 1.60


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _safe(numerator: float, denominator: float, default: float = 0.0) -> float:
    if not _finite(denominator) or abs(float(denominator)) <= _EPS:
        return default
    return float(numerator) / float(denominator)


def _body_fraction(bar: BarObservation) -> float:
    return abs(bar.close - bar.open) / max(bar.high - bar.low, _EPS)


def _close_location(bar: BarObservation) -> float:
    return (bar.close - bar.low) / max(bar.high - bar.low, _EPS)


def _flow_alignment(feature: FeatureObservation, side: int) -> float:
    candidates = []
    for value in (feature.flow_60s, feature.flow_open_10s):
        if _finite(value):
            candidates.append(side * float(value))
    return max(candidates) if candidates else -math.inf


def _feature_number(feature: FeatureObservation, name: str, default: float) -> float:
    value = float(getattr(feature, name, math.nan))
    return value if math.isfinite(value) else default


def pending_setup_invalidated(bar: BarObservation, side: int, stop: float) -> bool:
    """Cancel a passive parent before fill once its structural stop is touched."""
    if side > 0:
        return bar.low <= stop
    if side < 0:
        return bar.high >= stop
    return True


def minutes_to_next_funding(ts_event: int) -> float:
    moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
    elapsed = (moment.hour % 8) * 60 + moment.minute + moment.second / 60.0
    remaining = 8 * 60 - elapsed
    return remaining if remaining > 1e-9 else 8 * 60.0


def _with_diagnostics(
    decision: RouteDecision,
    *,
    state: str,
    score_bonus: float,
    additions: Mapping[str, object],
    stop: float | None = None,
) -> RouteDecision | None:
    entry = decision.entry_reference
    target = decision.objective_reference
    revised_stop = decision.stop_reference if stop is None else float(stop)
    if decision.side > 0 and not (revised_stop < entry < target):
        return None
    if decision.side < 0 and not (target < entry < revised_stop):
        return None
    risk = abs(entry - revised_stop)
    reward = decision.side * (target - entry)
    if risk <= 0.0 or reward <= 0.0:
        return None
    data = dict(decision.diagnostics)
    data.update(additions)
    data["raw_structural_r"] = reward / risk
    data["stop_atr"] = risk / decision.atr
    return replace(
        decision,
        state=state,
        score=decision.score + score_bonus,
        expected_target_r=reward / risk,
        stop_reference=revised_stop,
        diagnostics=data,
    )


def _sponsored_pullback(
    decision: RouteDecision,
    event_feature: FeatureObservation,
    confirmation_feature: FeatureObservation,
    config: InformedRouterConfig,
) -> RouteDecision | None:
    if not (event_feature.ready and confirmation_feature.ready):
        return None
    side = decision.side
    event_flow = _flow_alignment(event_feature, side)
    confirmation_flow = _flow_alignment(confirmation_feature, side)
    event_oi = _feature_number(event_feature, "oi_change_15m", -math.inf)
    confirmation_oi = _feature_number(confirmation_feature, "oi_change_15m", 0.0)
    efficiency = _feature_number(confirmation_feature, "efficiency_60s", 0.0)
    burst = _feature_number(event_feature, "notional_open_10s_burst", 1.0)
    premium_side = side * _feature_number(confirmation_feature, "premium_z", 0.0)
    impulse_atr = float(decision.diagnostics.get("impulse_atr", math.inf))
    if not (
        event_oi >= config.min_sponsor_oi_build
        and event_flow >= config.min_event_flow_alignment
        and confirmation_flow >= config.min_confirmation_flow_alignment
        and efficiency >= config.min_confirmation_efficiency
        and confirmation_oi >= -config.max_confirmation_oi_contraction
        and burst <= config.max_climax_burst
        and impulse_atr <= config.max_initiative_impulse_atr
        and premium_side <= config.max_side_premium_z
    ):
        return None
    return _with_diagnostics(
        decision,
        state="SPONSORED_FIRST_PULLBACK",
        score_bonus=0.35 + 0.35 * min(event_oi / config.min_sponsor_oi_build, 3.0),
        additions={
            "family": "SPONSORED_FIRST_PULLBACK",
            "event_oi_change_15m": event_oi,
            "event_flow_alignment": event_flow,
            "confirmation_flow_alignment": confirmation_flow,
            "confirmation_efficiency_60s": efficiency,
            "confirmation_oi_change_15m": confirmation_oi,
            "event_notional_burst": burst,
            "confirmation_side_premium_z": premium_side,
            "episode_key": f"{decision.symbol}:SPONSORED_FIRST_PULLBACK:{decision.episode_ts}",
            "source_state_repair": "INITIATIVE_SPONSORSHIP_NOT_LIQUIDATION_CLIMAX",
        },
    )


def _informed_failed_level(
    decision: RouteDecision,
    context: SymbolContext,
    event_feature: FeatureObservation,
    confirmation_feature: FeatureObservation,
    median_return: float,
    breadth_by_side: Mapping[int, float],
    config: InformedRouterConfig,
) -> RouteDecision | None:
    if not (event_feature.ready and confirmation_feature.ready):
        return None
    side = decision.side
    event_oi = _feature_number(event_feature, "oi_change_15m", math.inf)
    confirmation_oi = _feature_number(confirmation_feature, "oi_change_15m", 0.0)
    confirmation_flow = _flow_alignment(confirmation_feature, side)
    efficiency = _feature_number(confirmation_feature, "efficiency_60s", 0.0)
    premium_side = side * _feature_number(confirmation_feature, "premium_z", 0.0)
    level = float(decision.diagnostics.get("attacked_level", math.nan))
    if not math.isfinite(level):
        return None
    latest = context.bars15[-1]
    reaccept_depth = side * (latest.close - level) / context.atr
    relative_isolation = -side * (context.return_4h_atr - median_return)
    isolated = relative_isolation >= config.min_relative_isolation_atr
    broad_reversal = float(breadth_by_side.get(side, 0.0)) >= 0.50
    if not (
        event_oi <= -config.min_liquidation_oi_contraction
        and confirmation_flow >= config.min_reversal_flow_alignment
        and efficiency >= config.min_reversal_efficiency
        and confirmation_oi >= -config.max_confirmation_oi_contraction
        and reaccept_depth >= config.min_reaccept_depth_atr
        and (isolated or broad_reversal)
        and premium_side <= config.max_side_premium_z
    ):
        return None

    # Invalidation is acceptance back outside the attacked level, not a tiny
    # stop just beyond a wick. The wider hard stop must still leave honest
    # same-auction target space.
    if side > 0:
        stop = min(decision.stop_reference, level - config.failed_level_hard_stop_atr * context.atr)
    else:
        stop = max(decision.stop_reference, level + config.failed_level_hard_stop_atr * context.atr)
    revised = _with_diagnostics(
        decision,
        state="LIQUIDATION_FAILURE_REACCEPTANCE",
        score_bonus=0.40 + min(abs(event_oi) * 100.0, 0.70),
        stop=stop,
        additions={
            "family": "LIQUIDATION_FAILURE_REACCEPTANCE",
            "event_oi_change_15m": event_oi,
            "confirmation_oi_change_15m": confirmation_oi,
            "confirmation_flow_alignment": confirmation_flow,
            "confirmation_efficiency_60s": efficiency,
            "reaccept_depth_atr": reaccept_depth,
            "relative_isolation_atr": relative_isolation,
            "broad_reversal_fraction": float(breadth_by_side.get(side, 0.0)),
            "confirmation_side_premium_z": premium_side,
            "episode_key": (
                f"{decision.symbol}:LIQUIDATION_FAILURE_REACCEPTANCE:"
                f"{decision.diagnostics.get('reference','UNKNOWN')}:"
                f"{level:.12g}:{side}"
            ),
            "source_state_repair": "LEVERAGE_FLUSH_TO_REACCEPTANCE_FLOW_FLIP",
        },
    )
    if revised is None or revised.expected_target_r < config.failed_level_target_r_floor:
        return None
    return revised


def _session_start_ns(ts_event: int) -> int:
    moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
    start_hour = (moment.hour // 8) * 8
    return int(datetime(moment.year, moment.month, moment.day, start_hour, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def _opening_range_candidate(
    context: SymbolContext,
    *,
    breadth_by_side: Mapping[int, float],
    feature_at: FeatureProvider,
    confirmation_feature: FeatureObservation,
    config: InformedRouterConfig,
) -> RouteDecision | None:
    bars = context.bars15
    latest = bars[-1]
    session_start = _session_start_ns(latest.ts_event)
    opening_end = session_start + config.opening_range_minutes * MINUTE_NS
    if latest.ts_event < opening_end + 15 * MINUTE_NS:
        return None
    opening = [item for item in bars if session_start <= item.ts_event < opening_end]
    session = [item for item in bars if session_start <= item.ts_event <= latest.ts_event]
    if len(opening) < 4 or len(session) < 6:
        return None
    or_high = max(item.high for item in opening)
    or_low = min(item.low for item in opening)
    or_width = or_high - or_low
    if or_width < 0.65 * context.atr or or_width > 6.0 * context.atr:
        return None
    baseline_rows = [item.volume for item in bars[-32:-4] if item.volume > 0.0]
    baseline_volume = median(baseline_rows) if baseline_rows else context.volume_baseline

    before_confirmation = session[:-1]
    search = before_confirmation[-config.or_event_lookback_bars :]
    best: RouteDecision | None = None
    prior_start = session_start - 8 * HOUR_NS
    prior = [item for item in bars if prior_start <= item.ts_event < session_start]
    prior_high = max((item.high for item in prior), default=math.nan)
    prior_low = min((item.low for item in prior), default=math.nan)
    for event in search:
        for side, boundary in ((1, or_high), (-1, or_low)):
            acceptance = side * (event.close - boundary) / context.atr
            event_loc = _close_location(event) if side > 0 else 1.0 - _close_location(event)
            event_volume_ratio = _safe(event.volume, baseline_volume, 0.0)
            if not (
                config.min_or_acceptance_atr <= acceptance <= config.max_or_acceptance_atr
                and _body_fraction(event) >= config.min_or_body_fraction
                and event_loc >= config.min_or_close_location
                and event_volume_ratio >= config.min_or_volume_ratio
            ):
                continue
            if latest.ts_event <= event.ts_event:
                continue
            if side > 0:
                touched = latest.low <= boundary + config.or_retest_tolerance_atr * context.atr
                held = latest.close >= boundary + config.min_or_hold_depth_atr * context.atr
                confirmed = latest.close > latest.open and _close_location(latest) >= config.min_or_close_location
                entry = boundary + 0.04 * context.atr
                entry = min(entry, latest.close)
                stop = min(latest.low, boundary - config.or_hard_stop_atr * context.atr) - 0.05 * context.atr
                measured = boundary + or_width
                structural_targets = [value for value in (prior_high, measured) if math.isfinite(value) and value > entry]
                target = min(structural_targets) if structural_targets else math.nan
            else:
                touched = latest.high >= boundary - config.or_retest_tolerance_atr * context.atr
                held = latest.close <= boundary - config.min_or_hold_depth_atr * context.atr
                confirmed = latest.close < latest.open and _close_location(latest) <= 1.0 - config.min_or_close_location
                entry = boundary - 0.04 * context.atr
                entry = max(entry, latest.close)
                stop = max(latest.high, boundary + config.or_hard_stop_atr * context.atr) + 0.05 * context.atr
                measured = boundary - or_width
                structural_targets = [value for value in (prior_low, measured) if math.isfinite(value) and value < entry]
                target = max(structural_targets) if structural_targets else math.nan
            if not (touched and held and confirmed and math.isfinite(target)):
                continue
            event_feature = feature_at(context.symbol, event.ts_event)
            if not (event_feature.ready and confirmation_feature.ready):
                continue
            event_flow = _flow_alignment(event_feature, side)
            confirmation_flow = _flow_alignment(confirmation_feature, side)
            event_oi = _feature_number(event_feature, "oi_change_15m", 0.0)
            confirmation_oi = _feature_number(confirmation_feature, "oi_change_15m", 0.0)
            sponsored = event_oi >= config.min_or_sponsor_oi or event_flow >= config.min_or_flow_alignment
            if not (
                sponsored
                and confirmation_flow >= config.min_or_flow_alignment
                and confirmation_oi >= -config.max_confirmation_oi_contraction
                and float(breadth_by_side.get(side, 0.0)) >= config.min_or_breadth_fraction
            ):
                continue
            risk = abs(entry - stop)
            reward = side * (target - entry)
            if risk <= 0.0 or reward / risk < config.or_target_r_floor:
                continue
            score = (
                2.0
                + acceptance * 0.55
                + event_volume_ratio * 0.20
                + confirmation_flow * 0.65
                + float(breadth_by_side.get(side, 0.0)) * 0.55
                + min(max(event_oi, 0.0) * 100.0, 0.65)
            )
            decision = RouteDecision(
                symbol=context.symbol,
                state="OPENING_RANGE_ACCEPTANCE_RETEST",
                side=side,
                score=score,
                expected_target_r=reward / risk,
                atr=context.atr,
                entry_reference=entry,
                stop_reference=stop,
                objective_reference=target,
                episode_ts=event.ts_event,
                reasons=(
                    "COMPLETED_8H_SESSION_OPENING_RANGE",
                    "SPONSORED_15M_ACCEPTANCE_OUTSIDE_RANGE",
                    "LATER_15M_BOUNDARY_RETEST_HELD",
                ),
                diagnostics={
                    "family": "OPENING_RANGE_ACCEPTANCE_RETEST",
                    "opening_range_high": or_high,
                    "opening_range_low": or_low,
                    "opening_range_atr": or_width / context.atr,
                    "acceptance_atr": acceptance,
                    "event_volume_ratio": event_volume_ratio,
                    "event_oi_change_15m": event_oi,
                    "event_flow_alignment": event_flow,
                    "confirmation_flow_alignment": confirmation_flow,
                    "confirmation_oi_change_15m": confirmation_oi,
                    "breadth_fraction": float(breadth_by_side.get(side, 0.0)),
                    "raw_structural_r": reward / risk,
                    "stop_atr": risk / context.atr,
                    "policy_target_r_floor": config.or_target_r_floor,
                    "entry_policy": "PASSIVE_OPENING_RANGE_RETEST_LIMIT",
                    "event_confirmation_separated": True,
                    "non_scalping": True,
                    "episode_key": f"{context.symbol}:OPENING_RANGE:{session_start}:{side}",
                },
            )
            if best is None or (decision.score, decision.expected_target_r) > (best.score, best.expected_target_r):
                best = decision
    return best


def route_informed_universe(
    *,
    minute_bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    confirmation_features_by_symbol: Mapping[str, FeatureObservation],
    feature_at: FeatureProvider,
    config: InformedRouterConfig | None = None,
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    cfg = config or InformedRouterConfig()
    contexts: dict[str, SymbolContext] = {}
    for symbol, bars in minute_bars_by_symbol.items():
        context = _make_context(symbol, bars, cfg.price)
        if context is not None:
            contexts[symbol] = context
    if not contexts:
        return None, {}

    returns = [item.return_4h_atr for item in contexts.values()]
    median_return = median(returns)
    breadth_by_side = {
        1: sum(value > 0.15 for value in returns) / len(returns),
        -1: sum(value < -0.15 for value in returns) / len(returns),
    }
    decisions: dict[str, RouteDecision] = {}
    for symbol, context in contexts.items():
        confirmation = confirmation_features_by_symbol.get(symbol, FeatureObservation(0, ready=False))
        trend_side = 1 if context.return_4h_atr > 0.15 else -1 if context.return_4h_atr < -0.15 else 0
        candidates: list[RouteDecision] = []
        pullback = _first_pullback_candidate(
            context,
            peer_breadth=float(breadth_by_side.get(trend_side, 0.0)),
            config=cfg.price,
        )
        if pullback is not None:
            informed = _sponsored_pullback(pullback, feature_at(symbol, pullback.episode_ts), confirmation, cfg)
            if informed is not None:
                candidates.append(informed)
        failed = _failed_level_candidate(
            context,
            peer_breadth_by_side=breadth_by_side,
            config=cfg.price,
        )
        if failed is not None:
            informed = _informed_failed_level(
                failed,
                context,
                feature_at(symbol, failed.episode_ts),
                confirmation,
                median_return,
                breadth_by_side,
                cfg,
            )
            if informed is not None:
                candidates.append(informed)
        opening = _opening_range_candidate(
            context,
            breadth_by_side=breadth_by_side,
            feature_at=feature_at,
            confirmation_feature=confirmation,
            config=cfg,
        )
        if opening is not None:
            candidates.append(opening)
        if not candidates:
            continue
        selected = max(candidates, key=lambda item: (item.score, item.expected_target_r, item.state))
        if selected.score >= cfg.min_route_score:
            decisions[symbol] = selected

    ranked = sorted(
        decisions.values(),
        key=lambda item: (item.score, item.expected_target_r, item.symbol == "BTCUSDT", item.symbol),
        reverse=True,
    )
    if not ranked:
        return None, decisions
    if len(ranked) > 1 and ranked[0].side != ranked[1].side and ranked[0].score - ranked[1].score < cfg.ambiguity_score_gap:
        return None, decisions
    return ranked[0], decisions
