"""Dependency-free contracts for the public NFI squeeze-release adaptation."""
from __future__ import annotations

import math

from squeeze_release_strategy import FiveMinuteBar, causal_squeeze_release


STEP = 5 * 60 * 1_000_000_000
START = 1_700_000_000_000_000_000


def _history(*, release_close: float, release_high: float, release_low: float):
    bars: list[FiveMinuteBar] = []
    for index in range(43):
        high = 101.0
        low = 99.0
        # One old wide wick gives the completed compression a causal measured
        # range without changing its constant closing-price coil.
        if index == 25:
            high = 115.0
            low = 85.0
        bars.append(
            FiveMinuteBar(
                ts_event=START + index * STEP,
                open=100.0,
                high=high,
                low=low,
                close=100.0,
                volume=10.0,
            )
        )
    bars.append(
        FiveMinuteBar(
            ts_event=START + 43 * STEP,
            open=100.0,
            high=release_high,
            low=release_low,
            close=release_close,
            volume=20.0,
        )
    )
    return bars


def test_long_squeeze_release_has_coil_invalidation_and_measured_move() -> None:
    bars = _history(release_close=115.0, release_high=116.0, release_low=99.0)
    state = causal_squeeze_release(bars, current_ts=bars[-1].ts_event)
    assert state.ready is True
    assert state.squeeze_previous is True
    assert state.squeeze_current is False
    assert state.prior_squeeze_count >= 12
    assert state.side == 1
    assert state.coil_high == 115.0
    assert state.coil_low == 85.0
    assert state.stop < 115.0 < state.target
    assert state.target == 145.0
    assert math.isfinite(state.reward_risk) and state.reward_risk > 0.0


def test_short_squeeze_release_is_exact_mirror() -> None:
    bars = _history(release_close=85.0, release_high=101.0, release_low=84.0)
    state = causal_squeeze_release(bars, current_ts=bars[-1].ts_event)
    assert state.ready is True
    assert state.squeeze_previous is True
    assert state.squeeze_current is False
    assert state.side == -1
    assert 0.0 < state.target < 85.0 < state.stop
    assert state.target == 55.0


def test_unreleased_coil_is_not_actionable() -> None:
    bars = _history(release_close=104.0, release_high=105.0, release_low=99.0)
    state = causal_squeeze_release(bars, current_ts=bars[-1].ts_event)
    assert state.ready is True
    assert state.side == 0
    assert state.actionable is False


def test_future_stale_or_gapped_history_is_rejected() -> None:
    bars = _history(release_close=115.0, release_high=116.0, release_low=99.0)
    for mismatched in (bars[-1].ts_event - STEP, bars[-1].ts_event + STEP):
        try:
            causal_squeeze_release(bars, current_ts=mismatched)
        except ValueError:
            pass
        else:
            raise AssertionError(f"mismatched timestamp {mismatched} was accepted")

    gapped = list(bars)
    item = gapped[-2]
    gapped[-2] = FiveMinuteBar(
        ts_event=item.ts_event - STEP,
        open=item.open,
        high=item.high,
        low=item.low,
        close=item.close,
        volume=item.volume,
    )
    try:
        causal_squeeze_release(gapped, current_ts=gapped[-1].ts_event)
    except ValueError:
        pass
    else:
        raise AssertionError("non-contiguous squeeze history was accepted")


def test_nonfinite_release_input_is_rejected() -> None:
    bars = _history(release_close=115.0, release_high=116.0, release_low=99.0)
    bad = list(bars)
    item = bad[-1]
    bad[-1] = FiveMinuteBar(
        ts_event=item.ts_event,
        open=item.open,
        high=item.high,
        low=item.low,
        close=math.nan,
        volume=item.volume,
    )
    try:
        causal_squeeze_release(bad, current_ts=bad[-1].ts_event)
    except ValueError:
        pass
    else:
        raise AssertionError("non-finite squeeze release was accepted")
