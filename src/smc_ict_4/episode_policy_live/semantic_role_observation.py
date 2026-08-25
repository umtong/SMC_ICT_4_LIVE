"""Point-in-time cross-market role observation for structural opportunities.

The helper in this module only assembles causal observations.  It does not
approve, reject, rank, or resize an opportunity.  In particular, an absent
peer bar is never replaced with the candidate market and an absent event
endpoint is never approximated with the nearest close.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from .cross_market_roles import (
    CausalScalar,
    CrossMarketAuctionRoles,
    EventPrice,
    analyze_cross_market_roles,
)
from .directional_context import DirectionalContext, build_directional_context
from .domain import Bar, SYMBOLS
from .structural_campaign import Side, StructuralOpportunity


def _visible_interval_history(
    bars: Sequence[Bar],
    *,
    symbol: str,
    interval_minutes: int,
    decision_time_ns: int,
) -> tuple[Bar, ...] | None:
    """Return the completed causal history, or ``None`` when it is ambiguous."""

    visible = sorted(
        (
            bar
            for bar in bars
            if bar.symbol == symbol
            and bar.interval_minutes == interval_minutes
            and bar.close_time_ns <= decision_time_ns
        ),
        key=lambda bar: bar.close_time_ns,
    )
    if not visible:
        return None
    duration_ns = interval_minutes * 60_000_000_000
    for bar in visible:
        if bar.close_time_ns - bar.open_time_ns != duration_ns:
            return None
    for left, right in zip(visible, visible[1:]):
        if right.open_time_ns != left.close_time_ns:
            return None
    return tuple(visible)


def _event_path(
    bars: Sequence[Bar],
    *,
    interaction_time_ns: int,
    decision_time_ns: int,
) -> tuple[EventPrice, ...] | None:
    """Build a path with exact, jointly visible interaction and decision ends."""

    decision_bar = next(
        (bar for bar in bars if bar.close_time_ns == decision_time_ns),
        None,
    )
    if decision_bar is None:
        return None

    interaction_open = next(
        (bar for bar in bars if bar.open_time_ns == interaction_time_ns),
        None,
    )
    if interaction_open is not None:
        start = EventPrice(interaction_time_ns, interaction_open.open)
        continuation = [
            EventPrice(bar.close_time_ns, bar.close)
            for bar in bars
            if interaction_time_ns < bar.close_time_ns <= decision_time_ns
        ]
    else:
        interaction_close = next(
            (bar for bar in bars if bar.close_time_ns == interaction_time_ns),
            None,
        )
        if interaction_close is None:
            return None
        start = EventPrice(interaction_time_ns, interaction_close.close)
        continuation = [
            EventPrice(bar.close_time_ns, bar.close)
            for bar in bars
            if interaction_time_ns < bar.close_time_ns <= decision_time_ns
        ]
    path = (start, *continuation)
    if len(path) < 2 or path[-1].ts_ns != decision_time_ns:
        return None
    return path


def observe_cross_market_auction_roles(
    *,
    opportunity: StructuralOpportunity,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    interaction_time_ns: int,
    decision_time_ns: int,
    candidate_side: Side,
) -> CrossMarketAuctionRoles | None:
    """Observe synchronized semantic roles without making an admission decision.

    All four research-universe markets must expose the same completed bar
    timeline.  Direction is frozen at the last common completed bar strictly
    before the interaction; event leadership is measured from the exact
    interaction timestamp through the exact opportunity decision timestamp.
    Missing synchronization, history, or endpoint visibility returns ``None``.
    """

    if candidate_side != opportunity.side:
        raise ValueError("candidate_side must match opportunity.side")
    if decision_time_ns != opportunity.hypothesis_confirmation_time_ns:
        raise ValueError(
            "decision_time_ns must match opportunity hypothesis confirmation"
        )
    if interaction_time_ns < 0 or interaction_time_ns >= decision_time_ns:
        raise ValueError("interaction_time_ns must precede decision_time_ns")
    if set(bars_by_symbol) != set(SYMBOLS):
        return None

    decision_bars = {
        symbol: [
            bar
            for bar in bars_by_symbol[symbol]
            if bar.symbol == symbol and bar.close_time_ns == decision_time_ns
        ]
        for symbol in SYMBOLS
    }
    if any(len(items) != 1 for items in decision_bars.values()):
        return None
    intervals = {
        items[0].interval_minutes for items in decision_bars.values()
    }
    if len(intervals) != 1:
        return None
    interval_minutes = intervals.pop()

    histories: dict[str, tuple[Bar, ...]] = {}
    for symbol in SYMBOLS:
        history = _visible_interval_history(
            bars_by_symbol[symbol],
            symbol=symbol,
            interval_minutes=interval_minutes,
            decision_time_ns=decision_time_ns,
        )
        if history is None:
            return None
        histories[symbol] = history

    timelines = {
        tuple((bar.open_time_ns, bar.close_time_ns) for bar in history)
        for history in histories.values()
    }
    if len(timelines) != 1:
        return None

    prior_times = {
        max(
            (
                bar.close_time_ns
                for bar in history
                if bar.close_time_ns <= interaction_time_ns
            ),
            default=-1,
        )
        for history in histories.values()
    }
    if len(prior_times) != 1:
        return None
    prior_time_ns = prior_times.pop()
    if prior_time_ns < 0:
        return None

    event_paths: dict[str, tuple[EventPrice, ...]] = {}
    for symbol, history in histories.items():
        path = _event_path(
            history,
            interaction_time_ns=interaction_time_ns,
            decision_time_ns=decision_time_ns,
        )
        if path is None:
            return None
        event_paths[symbol] = path
    event_timelines = {
        tuple(point.ts_ns for point in path) for path in event_paths.values()
    }
    if len(event_timelines) != 1:
        return None

    contexts: dict[str, DirectionalContext] = {}
    trailing_quote_notionals: dict[str, CausalScalar] = {}
    event_return_scales: dict[str, CausalScalar] = {}
    try:
        for symbol in SYMBOLS:
            context = build_directional_context(
                symbol=symbol,
                side=candidate_side,
                decision_time_ns=prior_time_ns,
                bars_by_symbol=histories,
                interval_minutes=interval_minutes,
            )
            if context.trend_alignment is None:
                return None
            contexts[symbol] = context
            # Cross-asset leadership compares movement in each market's own
            # causal volatility unit.  Raw percentage ranks systematically
            # privilege SOL/XRP over BTC/ETH and do not describe ownership.
            # Quote concentration is retained only as an ordinal role.
            prior = [
                bar
                for bar in histories[symbol]
                if bar.close_time_ns <= prior_time_ns
            ]
            if not prior:
                return None
            if context.atr_price is None or prior[-1].close <= 0.0:
                return None
            bars_per_hour = 60 // interval_minutes
            if bars_per_hour <= 0 or len(prior) < bars_per_hour:
                return None
            trailing_quote_notionals[symbol] = CausalScalar(
                observed_time_ns=prior_time_ns,
                value=sum(bar.quote_volume for bar in prior[-bars_per_hour:]),
            )
            event_return_scales[symbol] = CausalScalar(
                observed_time_ns=prior_time_ns,
                value=context.atr_price / prior[-1].close,
            )
        roles = analyze_cross_market_roles(
            symbols=SYMBOLS,
            symbol=opportunity.symbol,
            side=candidate_side,
            sweep_time_ns=interaction_time_ns,
            decision_time_ns=decision_time_ns,
            event_paths=event_paths,
            directional_contexts=contexts,
            trailing_quote_notionals=trailing_quote_notionals,
            event_return_scales=event_return_scales,
        )
    except ValueError:
        return None
    return roles if roles.synchronized_event_complete else None


__all__ = ["observe_cross_market_auction_roles"]
