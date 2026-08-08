"""Trader-derived non-scalping price-auction router for Candidate 39 V4.

The public module re-exports the causal aggregation/core contracts and the two
independent scenario families, then arbitrates them under the four-symbol
single-position account policy.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from router import BarObservation, RouteDecision
from router_v4_core import (
    FIFTEEN_MINUTES_NS,
    MINUTE_NS,
    LevelReference,
    SymbolContext,
    TraderDerivedConfig,
    _make_context,
    aggregate_completed_15m,
)
from router_v4_families import (
    _failed_level_candidate,
    _first_pullback_candidate,
)

def route_trader_derived_universe(
    *,
    minute_bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    config: TraderDerivedConfig | None = None,
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    """Return at most one actionable decision across the four-symbol account."""
    cfg = config or TraderDerivedConfig()
    contexts: dict[str, SymbolContext] = {}
    decisions: dict[str, RouteDecision] = {}
    for symbol, minute_bars in minute_bars_by_symbol.items():
        context = _make_context(symbol, minute_bars, cfg)
        if context is not None:
            contexts[symbol] = context

    if not contexts:
        return None, decisions

    returns = {symbol: context.return_4h_atr for symbol, context in contexts.items()}
    breadth_by_side = {
        1: sum(value > 0.15 for value in returns.values()) / len(returns),
        -1: sum(value < -0.15 for value in returns.values()) / len(returns),
    }

    for symbol, context in contexts.items():
        trend_side = 1 if context.return_4h_atr > 0.15 else -1 if context.return_4h_atr < -0.15 else 0
        pullback = _first_pullback_candidate(
            context,
            peer_breadth=float(breadth_by_side.get(trend_side, 0.0)),
            config=cfg,
        )
        failed = _failed_level_candidate(
            context,
            peer_breadth_by_side=breadth_by_side,
            config=cfg,
        )
        candidates = [item for item in (pullback, failed) if item is not None]
        if not candidates:
            continue
        decision = max(
            candidates,
            key=lambda item: (
                item.score,
                item.expected_target_r,
                item.state == "FAILED_LEVEL_REACCEPTANCE",
            ),
        )
        if decision.score + 1e-12 >= cfg.min_route_score:
            decisions[symbol] = decision

    actionable = sorted(
        decisions.values(),
        key=lambda item: (
            item.score,
            item.expected_target_r,
            item.symbol == "BTCUSDT",
            item.symbol,
        ),
        reverse=True,
    )
    if not actionable:
        return None, decisions
    if len(actionable) > 1:
        top, second = actionable[0], actionable[1]
        if top.side != second.side and top.score - second.score < cfg.ambiguity_score_gap:
            return None, decisions
    return actionable[0], decisions
