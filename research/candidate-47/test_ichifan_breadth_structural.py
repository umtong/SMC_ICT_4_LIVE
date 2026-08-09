"""Dependency-free causal contracts for Candidate 47 market breadth."""
from __future__ import annotations

import math

from ichifan_breadth_structural_strategy import causal_one_minute_breadth
from router import BarObservation


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TS = 1_700_000_000_000_000_000


def _bar(*, symbol_index: int, up: bool, ts_event: int = TS) -> BarObservation:
    opening = 100.0 + symbol_index
    closing = opening + (1.0 if up else -1.0)
    return BarObservation(
        ts_event=ts_event,
        open=opening,
        high=max(opening, closing) + 0.5,
        low=min(opening, closing) - 0.5,
        close=closing,
        volume=10.0,
    )


def _universe(flags: tuple[bool, bool, bool, bool], ts_event: int = TS):
    return {
        symbol: (_bar(symbol_index=index, up=flags[index], ts_event=ts_event),)
        for index, symbol in enumerate(SYMBOLS)
    }


def test_two_of_four_positive_is_confirmed() -> None:
    result = causal_one_minute_breadth(
        bars_by_symbol=_universe((True, False, True, False)),
        current_ts=TS,
    )
    assert result.confirmed is True
    assert result.positive_count == 2
    assert result.positive_symbols == ("BTCUSDT", "SOLUSDT")
    assert result.nonpositive_symbols == ("ETHUSDT", "XRPUSDT")
    assert all(math.isfinite(value) for _, value in result.returns_bps)


def test_one_of_four_positive_is_rejected() -> None:
    result = causal_one_minute_breadth(
        bars_by_symbol=_universe((False, False, True, False)),
        current_ts=TS,
    )
    assert result.confirmed is False
    assert result.positive_count == 1
    assert result.positive_symbols == ("SOLUSDT",)


def test_future_or_stale_latest_observation_is_rejected() -> None:
    for mismatched_ts in (TS - 60_000_000_000, TS + 60_000_000_000):
        universe = _universe((True, True, False, False))
        universe["BTCUSDT"] = (
            _bar(symbol_index=0, up=True, ts_event=mismatched_ts),
        )
        try:
            causal_one_minute_breadth(
                bars_by_symbol=universe,
                current_ts=TS,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"mismatched timestamp {mismatched_ts} was accepted")


def test_missing_or_invalid_price_is_rejected() -> None:
    missing = _universe((True, True, False, False))
    del missing["XRPUSDT"]
    try:
        causal_one_minute_breadth(bars_by_symbol=missing, current_ts=TS)
    except ValueError:
        pass
    else:
        raise AssertionError("missing symbol was accepted")

    for invalid in (math.nan, math.inf, 0.0, -1.0):
        universe = _universe((True, True, False, False))
        universe["ETHUSDT"] = (
            BarObservation(
                ts_event=TS,
                open=invalid,
                high=102.0,
                low=99.0,
                close=101.0,
                volume=10.0,
            ),
        )
        try:
            causal_one_minute_breadth(bars_by_symbol=universe, current_ts=TS)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid price {invalid} was accepted")


def test_future_append_cannot_change_prior_breadth_state() -> None:
    base = _universe((True, False, True, False))
    prior = causal_one_minute_breadth(bars_by_symbol=base, current_ts=TS)

    future_ts = TS + 60_000_000_000
    extended = {
        symbol: history
        + (_bar(symbol_index=index, up=not (index % 2 == 0), ts_event=future_ts),)
        for index, (symbol, history) in enumerate(base.items())
    }
    truncated = {symbol: history[:-1] for symbol, history in extended.items()}
    replay = causal_one_minute_breadth(
        bars_by_symbol=truncated,
        current_ts=TS,
    )
    assert replay == prior
