"""Dependency-free contracts for the public ichiV2 causal adaptation."""
from __future__ import annotations

from dataclasses import fields
import math

from ichifan_strategy import (
    FiveMinuteBar,
    FanState,
    aggregate_five_minute,
    fan_states,
)
from router import BarObservation

MINUTE_NS = 60 * 1_000_000_000


def _minute(index: int, base: float = 100.0) -> BarObservation:
    opening = base + 0.1 * index
    close = opening + 0.04
    return BarObservation(
        ts_event=index * MINUTE_NS,
        open=opening,
        high=close + 0.03,
        low=opening - 0.02,
        close=close,
        volume=10.0 + index,
    )


def _five(index: int, growth: float = 1.0015) -> FiveMinuteBar:
    opening = 100.0 * growth**index
    close = opening * 1.0008
    return FiveMinuteBar(
        ts_event=(index * 5 + 4) * MINUTE_NS,
        open=opening,
        high=max(opening, close) * 1.0010,
        low=min(opening, close) * 0.9990,
        close=close,
        volume=1000.0 + index,
    )


def _same_number(left: float, right: float) -> bool:
    if math.isnan(left) and math.isnan(right):
        return True
    return abs(left - right) <= 1e-12 * max(1.0, abs(left), abs(right))


def _same_state(left: FanState, right: FanState) -> bool:
    for item in fields(FanState):
        a = getattr(left, item.name)
        b = getattr(right, item.name)
        if isinstance(a, float):
            if not _same_number(a, b):
                return False
        elif a != b:
            return False
    return True


def test_exact_five_minute_aggregation_and_partial_bin_rejection() -> None:
    bars = [_minute(index) for index in range(9)]
    aggregated = aggregate_five_minute(bars)
    assert len(aggregated) == 1
    first = aggregated[0]
    assert first.ts_event == 4 * MINUTE_NS
    assert first.open == bars[0].open
    assert first.close == bars[4].close
    assert first.high == max(item.high for item in bars[:5])
    assert first.low == min(item.low for item in bars[:5])
    assert first.volume == sum(item.volume for item in bars[:5])

    complete = aggregate_five_minute([_minute(index) for index in range(10)])
    assert len(complete) == 2
    assert complete[-1].ts_event == 9 * MINUTE_NS


def test_gap_bin_is_rejected_without_discarding_later_complete_bin() -> None:
    bars = [_minute(index) for index in (0, 1, 2, 4, 5, 6, 7, 8, 9)]
    aggregated = aggregate_five_minute(bars)
    assert len(aggregated) == 1
    assert aggregated[0].ts_event == 9 * MINUTE_NS
    assert aggregated[0].open == bars[4].open


def test_appending_future_bars_cannot_change_existing_states() -> None:
    prefix = [_five(index) for index in range(180)]
    extended = [*prefix, *[_five(index) for index in range(180, 205)]]
    before = fan_states(prefix)
    after = fan_states(extended)
    assert len(before) == len(prefix)
    assert len(after) == len(extended)
    for index, state in enumerate(before):
        assert _same_state(state, after[index]), f"future data changed state {index}"


def test_current_bar_extreme_cannot_enter_current_shifted_signal() -> None:
    bars = [_five(index) for index in range(180)]
    ordinary = fan_states(bars)[-1]
    last = bars[-1]
    mutated = [
        *bars[:-1],
        FiveMinuteBar(
            ts_event=last.ts_event,
            open=last.open * 0.2,
            high=last.high * 8.0,
            low=last.low * 0.1,
            close=last.close * 5.0,
            volume=last.volume * 1000.0,
        ),
    ]
    changed = fan_states(mutated)[-1]
    assert _same_state(ordinary, changed)


def test_long_warmup_produces_finite_causal_state() -> None:
    states = fan_states([_five(index) for index in range(190)])
    assert len(states) == 190
    ready = [state for state in states if state.ready]
    assert ready
    latest = ready[-1]
    assert math.isfinite(latest.fan_magnitude)
    assert math.isfinite(latest.fan_gain)
    assert math.isfinite(latest.trend_close_5m)
    assert math.isfinite(latest.trend_close_90m)
    assert math.isfinite(latest.trend_close_8h)
    assert math.isfinite(latest.cloud_a)
    assert math.isfinite(latest.cloud_b)
