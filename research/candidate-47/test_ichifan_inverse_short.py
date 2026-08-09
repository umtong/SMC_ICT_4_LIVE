"""Contracts for the reciprocal-price short mirror."""
from __future__ import annotations

import math

from ichifan_strategy import FiveMinuteBar, fan_states
from ichifan_inverse_short_strategy import reciprocal_bars, causal_inverse_short_stop


def _bar(index: int, decay: float = 0.9985) -> FiveMinuteBar:
    opening = 100.0 * decay**index
    close = opening * 0.9992
    return FiveMinuteBar(
        ts_event=(index * 5 + 4) * 60 * 1_000_000_000,
        open=opening,
        high=max(opening, close) * 1.001,
        low=min(opening, close) * 0.999,
        close=close,
        volume=1000.0 + index,
    )


def test_reciprocal_ohlc_geometry() -> None:
    bar = _bar(1)
    mapped = reciprocal_bars([bar])[0]
    assert math.isclose(mapped.open, 1.0 / bar.open)
    assert math.isclose(mapped.close, 1.0 / bar.close)
    assert math.isclose(mapped.high, 1.0 / bar.low)
    assert math.isclose(mapped.low, 1.0 / bar.high)
    assert mapped.low <= min(mapped.open, mapped.close) <= max(mapped.open, mapped.close) <= mapped.high


def test_future_bars_cannot_change_inverse_history() -> None:
    prefix = [_bar(index) for index in range(180)]
    before = fan_states(reciprocal_bars(prefix))
    after = fan_states(reciprocal_bars([*prefix, *[_bar(index) for index in range(180, 205)]]))
    assert len(before) == 180
    for index, left in enumerate(before):
        right = after[index]
        assert left.ts_event == right.ts_event
        assert left.ready == right.ready
        assert left.entry == right.entry
        assert left.exit_cross_down == right.exit_cross_down
        for name in ("fan_magnitude", "fan_gain", "trend_close_5m", "trend_close_90m", "trend_close_8h", "cloud_a", "cloud_b", "score"):
            a, b = getattr(left, name), getattr(right, name)
            assert (math.isnan(a) and math.isnan(b)) or math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


def test_mapped_stop_is_above_short_entry_and_capped() -> None:
    entry = 100.0
    stop, geometry = causal_inverse_short_stop(
        entry=entry,
        signal_bar_high=102.0,
        inverse_trend_close_90m=1.0 / 101.0,
        inverse_cloud_a=1.0 / 103.0,
        inverse_cloud_b=1.0 / 102.5,
    )
    assert entry < stop <= 110.0
    assert math.isclose(geometry["structural_stop_fraction"], (stop - entry) / entry)


def test_nonpositive_price_is_rejected() -> None:
    try:
        causal_inverse_short_stop(
            entry=0.0,
            signal_bar_high=1.0,
            inverse_trend_close_90m=1.0,
            inverse_cloud_a=1.0,
            inverse_cloud_b=1.0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("nonpositive entry accepted")
