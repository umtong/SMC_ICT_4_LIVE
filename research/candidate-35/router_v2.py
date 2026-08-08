"""Candidate 35b: cross-context, sponsored, cost-aware continuation router.

The original Candidate 35 classifier is retained as an observation primitive, not
as the final trading policy.  This router fixes three structural defects exposed
by the first Nautilus diagnostic:

* a quarter-hour clock phase is not standalone alpha;
* a price breakout without same-direction aggressor sponsorship is not accepted;
* raw price R/R is not economic R/R when round-trip costs are large.

Only accepted continuation can reach execution.  Exhaustion reversals remain
observable in diagnostics but fail closed until a separate, economically complete
reversal scenario is designed.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from statistics import median
from typing import Mapping, Sequence

from router import (
    BarObservation,
    FeatureObservation,
    RouteConfig as LegacyRouteConfig,
    RouteDecision,
    causal_atr,
    route_universe as legacy_route_universe,
)


@dataclass(frozen=True, slots=True)
class RouteConfig:
    # Legacy observation/classification parameters.
    atr_period: int = 30
    prior_bars: int = 15
    response_bars: int = 3
    min_impulse_atr_continuation: float = 0.75
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.12
    min_participation_ratio: float = 1.05
    min_route_score: float = 3.10
    ambiguity_score_gap: float = 0.20
    continuation_target_r: float = 2.20
    reversal_target_r: float = 1.80

    # Candidate 35b policy.
    context_bars: int = 60
    context_deadband_atr: float = 0.25
    min_context_median_atr: float = 0.50
    min_context_breadth: int = 3
    min_sponsored_flow: float = 0.02
    min_sponsored_return_bps: float = 0.50
    min_net_reward_r: float = 1.25
    all_in_cost_bps_each_side: float = 7.50
    adverse_slippage_bps_each_side: float = 2.50
    funding_reserve_bps: float = 1.00
    allow_reversal: bool = False

    def legacy(self) -> LegacyRouteConfig:
        return LegacyRouteConfig(
            atr_period=self.atr_period,
            prior_bars=self.prior_bars,
            response_bars=self.response_bars,
            min_impulse_atr_continuation=self.min_impulse_atr_continuation,
            min_impulse_atr_reversal=self.min_impulse_atr_reversal,
            min_response_atr=self.min_response_atr,
            min_participation_ratio=self.min_participation_ratio,
            min_route_score=self.min_route_score,
            ambiguity_score_gap=self.ambiguity_score_gap,
            continuation_target_r=self.continuation_target_r,
            reversal_target_r=self.reversal_target_r,
        )


def economic_net_reward_r(
    decision: RouteDecision,
    config: RouteConfig,
) -> tuple[float, float, float]:
    """Return expected net reward/R, planned loss/unit and net profit/unit."""
    side = int(decision.side)
    entry = float(decision.entry_reference)
    stop = float(decision.stop_reference)
    target = float(decision.objective_reference)
    if side not in (-1, 1) or not all(
        math.isfinite(value) and value > 0.0 for value in (entry, stop, target)
    ):
        return math.nan, math.nan, math.nan

    fee_rate = config.all_in_cost_bps_each_side / 10_000.0
    slip_rate = config.adverse_slippage_bps_each_side / 10_000.0
    funding_rate = config.funding_reserve_bps / 10_000.0

    adverse_entry = entry * (1.0 + side * slip_rate)
    adverse_stop = stop * (1.0 - side * slip_rate)
    planned_loss = (
        abs(adverse_entry - adverse_stop)
        + fee_rate * (abs(adverse_entry) + abs(adverse_stop))
        + funding_rate * abs(entry)
    )
    adverse_target = target * (1.0 - side * slip_rate)
    net_profit = (
        side * (adverse_target - adverse_entry)
        - fee_rate * (abs(adverse_entry) + abs(adverse_target))
        - funding_rate * abs(entry)
    )
    if not math.isfinite(planned_loss) or planned_loss <= 0.0:
        return math.nan, planned_loss, net_profit
    return net_profit / planned_loss, planned_loss, net_profit


def _context_score(
    bars: Sequence[BarObservation],
    config: RouteConfig,
) -> float:
    """Measure the independent pre-episode context in causal ATR units."""
    remove = config.prior_bars + config.response_bars
    required = remove + config.context_bars + config.atr_period + 1
    if len(bars) < required:
        return math.nan
    history = bars[:-remove]
    atr = causal_atr(history, config.atr_period)
    if not math.isfinite(atr) or atr <= 0.0:
        return math.nan
    return (history[-1].close - history[-1 - config.context_bars].close) / atr


def _response_opening_return_bps(
    bars: Sequence[BarObservation],
    config: RouteConfig,
) -> float:
    if len(bars) < config.response_bars:
        return math.nan
    opening = bars[-config.response_bars]
    if (
        not math.isfinite(opening.open)
        or not math.isfinite(opening.close)
        or opening.open <= 0.0
        or opening.close <= 0.0
    ):
        return math.nan
    return math.log(opening.close / opening.open) * 10_000.0


def _reject(
    decision: RouteDecision,
    reason: str,
    diagnostics: Mapping[str, float | int | bool | str],
) -> RouteDecision:
    merged = dict(decision.diagnostics)
    merged.update(diagnostics)
    merged["v2_rejection_reason"] = reason
    return replace(
        decision,
        state="UNRESOLVED",
        side=0,
        score=0.0,
        expected_target_r=0.0,
        stop_reference=math.nan,
        objective_reference=math.nan,
        reasons=(reason,),
        diagnostics=merged,
    )


def route_universe(
    *,
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    """Route one coherent account decision after independent context checks."""
    _, legacy = legacy_route_universe(
        bars_by_symbol=bars_by_symbol,
        features_by_symbol=features_by_symbol,
        config=config.legacy(),
    )
    context_scores = {
        symbol: _context_score(bars, config)
        for symbol, bars in bars_by_symbol.items()
    }

    decisions: dict[str, RouteDecision] = {}
    actionable: list[RouteDecision] = []
    ranks: dict[str, float] = {}

    for symbol, decision in legacy.items():
        diagnostics: dict[str, float | int | bool | str] = {
            "v2_policy": "CROSS_CONTEXT_SPONSORED_COST_AWARE_CONTINUATION",
            "context_bars": config.context_bars,
        }
        if not decision.actionable:
            decisions[symbol] = decision
            continue
        if decision.state != "PHASE_ACCEPTED_CONTINUATION" and not config.allow_reversal:
            decisions[symbol] = _reject(
                decision,
                "REVERSAL_FAMILY_DISABLED_UNTIL_ECONOMIC_SCENARIO_EXISTS",
                diagnostics,
            )
            continue

        side = int(decision.side)
        directional = [
            side * score
            for score in context_scores.values()
            if math.isfinite(score)
        ]
        aligned = sum(
            value > config.context_deadband_atr for value in directional
        )
        context_median = median(directional) if directional else math.nan
        diagnostics.update(
            {
                "context_breadth": aligned,
                "context_median_atr": context_median,
                "symbol_context_atr": context_scores.get(symbol, math.nan),
            },
        )
        if (
            aligned < config.min_context_breadth
            or not math.isfinite(context_median)
            or context_median < config.min_context_median_atr
        ):
            decisions[symbol] = _reject(
                decision,
                "PRE_EPISODE_CROSS_ASSET_CONTEXT_NOT_ALIGNED",
                diagnostics,
            )
            continue

        feature = features_by_symbol.get(symbol)
        response_return_bps = _response_opening_return_bps(
            bars_by_symbol[symbol],
            config,
        )
        flow_alignment = (
            side * float(feature.flow_60s)
            if feature is not None and math.isfinite(float(feature.flow_60s))
            else math.nan
        )
        response_alignment_bps = side * response_return_bps
        diagnostics.update(
            {
                "opening_response_flow_alignment": flow_alignment,
                "opening_response_return_alignment_bps": response_alignment_bps,
            },
        )
        if (
            not math.isfinite(flow_alignment)
            or flow_alignment < config.min_sponsored_flow
            or not math.isfinite(response_alignment_bps)
            or response_alignment_bps < config.min_sponsored_return_bps
        ):
            decisions[symbol] = _reject(
                decision,
                "OPENING_RESPONSE_NOT_SPONSORED_BY_FLOW_AND_PRICE",
                diagnostics,
            )
            continue

        net_r, planned_loss, expected_profit = economic_net_reward_r(
            decision,
            config,
        )
        diagnostics.update(
            {
                "economic_net_reward_r": net_r,
                "economic_planned_loss_per_unit": planned_loss,
                "economic_expected_profit_per_unit": expected_profit,
            },
        )
        if not math.isfinite(net_r) or net_r < config.min_net_reward_r:
            decisions[symbol] = _reject(
                decision,
                "OBJECTIVE_SPACE_INSUFFICIENT_AFTER_COSTS",
                diagnostics,
            )
            continue

        accepted = replace(
            decision,
            reasons=(
                "PRE_EPISODE_CROSS_ASSET_CONTEXT",
                "QUARTER_HOUR_ACCEPTANCE",
                "OPENING_RESPONSE_SPONSORED",
                "NET_OBJECTIVE_SPACE_CONFIRMED",
            ),
            diagnostics={**dict(decision.diagnostics), **diagnostics},
        )
        decisions[symbol] = accepted
        actionable.append(accepted)
        ranks[symbol] = accepted.score * net_r

    if not actionable:
        return None, decisions
    actionable.sort(
        key=lambda item: (
            ranks[item.symbol],
            item.score,
            1 if item.symbol == "BTCUSDT" else 0,
        ),
        reverse=True,
    )
    winner = actionable[0]
    if len(actionable) > 1:
        gap = ranks[winner.symbol] - ranks[actionable[1].symbol]
        if gap < config.ambiguity_score_gap:
            return None, decisions
    return winner, decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "economic_net_reward_r",
    "route_universe",
]
