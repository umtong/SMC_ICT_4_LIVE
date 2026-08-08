from __future__ import annotations

import math

from router import BarObservation
from router_v4 import (
    FIFTEEN_MINUTES_NS,
    MINUTE_NS,
    SymbolContext,
    TraderDerivedConfig,
    _failed_level_candidate,
    _first_pullback_candidate,
    _make_context,
    aggregate_completed_15m,
    route_trader_derived_universe,
)

MIN15 = FIFTEEN_MINUTES_NS


def _bar15(
    index: int,
    open_: float,
    close: float,
    *,
    low_pad: float = 0.15,
    high_pad: float = 0.15,
    volume: float = 100.0,
) -> BarObservation:
    return BarObservation(
        ts_event=index * MIN15,
        open=open_,
        high=max(open_, close) + high_pad,
        low=min(open_, close) - low_pad,
        close=close,
        volume=volume,
    )


def _expand(bars15: list[BarObservation]) -> list[BarObservation]:
    result: list[BarObservation] = []
    for item in bars15:
        bucket = item.ts_event // MIN15
        start = bucket * MIN15
        previous = item.open
        upper_extra = item.high - max(item.open, item.close)
        lower_extra = min(item.open, item.close) - item.low
        for minute in range(15):
            fraction = (minute + 1) / 15.0
            close = item.open + (item.close - item.open) * fraction
            open_ = previous
            high = max(open_, close)
            low = min(open_, close)
            if minute == 7:
                high += max(upper_extra, 0.0)
                low -= max(lower_extra, 0.0)
            result.append(
                BarObservation(
                    ts_event=start + minute * MINUTE_NS,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=item.volume / 15.0,
                )
            )
            previous = close
    return result


def _trend_bars15(*, deep_pullback: bool = False) -> list[BarObservation]:
    bars: list[BarObservation] = []
    price = 100.0
    for index in range(108):
        open_ = price
        close = price + 0.035 + 0.01 * math.sin(index / 5.0)
        bars.append(
            _bar15(
                index,
                open_,
                close,
                volume=100.0 + 5.0 * math.sin(index),
            )
        )
        price = close
    for _ in range(8):
        index = len(bars)
        open_ = price
        close = price + 0.38
        bars.append(
            _bar15(
                index,
                open_,
                close,
                low_pad=0.08,
                high_pad=0.12,
                volume=155.0,
            )
        )
        price = close
    pullback = (-0.85, -0.65, -0.55) if deep_pullback else (-0.35, -0.28, -0.15)
    for change in pullback:
        index = len(bars)
        open_ = price
        close = price + change
        bars.append(
            _bar15(
                index,
                open_,
                close,
                low_pad=0.12,
                high_pad=0.08,
                volume=90.0,
            )
        )
        price = close
    index = len(bars)
    bars.append(
        _bar15(
            index,
            price - 0.05,
            price + 0.42,
            low_pad=0.08,
            high_pad=0.06,
            volume=130.0,
        )
    )
    return bars


def _failed_upper_bars15(*, weak_target_space: bool = False) -> list[BarObservation]:
    bars: list[BarObservation] = []
    price = 100.0
    for index in range(96):
        open_ = price
        close = 100.0 + 0.55 * math.sin(index / 8.0)
        high = min(101.75, max(open_, close) + 0.22)
        low = max(98.25, min(open_, close) - 0.22)
        bars.append(
            BarObservation(
                ts_event=index * MIN15,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=100.0 + 5.0 * math.sin(index),
            )
        )
        price = close
    prior_high = max(item.high for item in bars)
    prior_low = min(item.low for item in bars)
    for index in range(96, 111):
        open_ = price
        close = 100.0 + 0.35 * math.sin(index / 4.0)
        bars.append(
            BarObservation(
                ts_event=index * MIN15,
                open=open_,
                high=min(max(open_, close) + 0.18, prior_high - 0.03),
                low=max(min(open_, close) - 0.18, prior_low + 0.03),
                close=close,
                volume=105.0,
            )
        )
        price = close
    event_high = prior_high + (0.65 if weak_target_space else 0.30)
    bars.append(
        BarObservation(
            ts_event=111 * MIN15,
            open=100.45,
            high=event_high,
            low=100.35,
            close=prior_high - 0.18,
            volume=150.0,
        )
    )
    bars.append(
        BarObservation(
            ts_event=112 * MIN15,
            open=prior_high - 0.08,
            high=prior_high + 0.04,
            low=prior_high - 0.60,
            close=prior_high - 0.52,
            volume=145.0,
        )
    )
    return bars


def _context(symbol: str, bars15: list[BarObservation]) -> SymbolContext:
    config = TraderDerivedConfig()
    minute = _expand(bars15)
    context = _make_context(symbol, minute, config)
    assert context is not None
    return context


def test_exact_15m_aggregation_rejects_incomplete_bucket():
    minute = _expand(_trend_bars15()[:2])
    complete = aggregate_completed_15m(minute)
    assert len(complete) == 2
    incomplete = aggregate_completed_15m(minute[:-1])
    assert len(incomplete) == 1


def test_first_pullback_requires_prior_initiative_and_distinct_confirmation():
    context = _context("BTCUSDT", _trend_bars15())
    decision = _first_pullback_candidate(
        context,
        peer_breadth=1.0,
        config=TraderDerivedConfig(),
    )
    assert decision is not None
    assert decision.state == "FIRST_PULLBACK_CONTINUATION"
    assert decision.side == 1
    assert decision.episode_ts < context.bars15[-1].ts_event
    assert decision.entry_reference <= context.bars15[-1].close
    assert decision.stop_reference < decision.entry_reference < decision.objective_reference
    assert decision.diagnostics["event_confirmation_separated"] is True


def test_deep_pullback_invalidates_continuation_instead_of_threshold_rescue():
    context = _context("BTCUSDT", _trend_bars15(deep_pullback=True))
    decision = _first_pullback_candidate(
        context,
        peer_breadth=1.0,
        config=TraderDerivedConfig(),
    )
    assert decision is None


def test_failed_prior_day_attack_reacceptance_and_later_retest():
    context = _context("ETHUSDT", _failed_upper_bars15())
    decision = _failed_level_candidate(
        context,
        peer_breadth_by_side={1: 0.25, -1: 0.75},
        config=TraderDerivedConfig(),
    )
    assert decision is not None
    assert decision.state == "FAILED_LEVEL_REACCEPTANCE"
    assert decision.side == -1
    assert decision.diagnostics["reference"] == "PRIOR_UTC_DAY"
    assert decision.episode_ts < context.bars15[-1].ts_event
    assert decision.objective_reference < decision.entry_reference < decision.stop_reference
    assert decision.diagnostics["raw_structural_r"] >= 1.55


def test_failed_level_does_not_invent_a_farther_target_to_save_bad_geometry():
    context = _context("ETHUSDT", _failed_upper_bars15(weak_target_space=True))
    decision = _failed_level_candidate(
        context,
        peer_breadth_by_side={1: 0.25, -1: 0.75},
        config=TraderDerivedConfig(),
    )
    assert decision is None


def test_universe_arbitration_returns_only_one_position_candidate():
    trend = _expand(_trend_bars15())
    failed = _expand(_failed_upper_bars15())
    winner, decisions = route_trader_derived_universe(
        minute_bars_by_symbol={
            "BTCUSDT": trend,
            "ETHUSDT": failed,
            "SOLUSDT": trend,
            "XRPUSDT": failed,
        },
        config=TraderDerivedConfig(),
    )
    assert winner is not None
    assert winner.symbol in decisions
    assert len(decisions) >= 2
    assert sum(item.actionable for item in decisions.values()) == len(decisions)


def test_price_only_router_does_not_require_oi_funding_or_cvd_fields():
    winner, decisions = route_trader_derived_universe(
        minute_bars_by_symbol={
            "BTCUSDT": _expand(_trend_bars15()),
            "ETHUSDT": _expand(_trend_bars15()),
            "SOLUSDT": _expand(_trend_bars15()),
            "XRPUSDT": _expand(_trend_bars15()),
        }
    )
    assert winner is not None
    assert decisions
    assert all(item.diagnostics["non_scalping"] is True for item in decisions.values())


def test_every_entry_reference_is_passive_at_confirmation_close():
    for symbol, bars15 in (
        ("BTCUSDT", _trend_bars15()),
        ("ETHUSDT", _failed_upper_bars15()),
    ):
        context = _context(symbol, bars15)
        if symbol == "BTCUSDT":
            decision = _first_pullback_candidate(
                context,
                peer_breadth=1.0,
                config=TraderDerivedConfig(),
            )
        else:
            decision = _failed_level_candidate(
                context,
                peer_breadth_by_side={1: 0.25, -1: 0.75},
                config=TraderDerivedConfig(),
            )
        assert decision is not None
        current = context.bars15[-1].close
        assert decision.side * (current - decision.entry_reference) >= -1e-12
