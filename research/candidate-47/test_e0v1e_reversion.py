"""Dependency-free indicator and causality contracts for E0V1E adaptation."""
from __future__ import annotations

import math

from e0v1e_reversion_strategy import (
    FiveMinuteBar,
    IndicatorSnapshot,
    _cti,
    _wilder_rsi,
    causal_e0v1e_state,
    e0v1e_branches,
)


STEP = 5 * 60 * 1_000_000_000
START = 1_700_000_000_000_000_000


def _bar(index: int, close: float, *, low: float | None = None) -> FiveMinuteBar:
    opening = close if index == 0 else close + 0.02
    actual_low = min(opening, close) - 0.20 if low is None else low
    return FiveMinuteBar(
        ts_event=START + index * STEP,
        open=opening,
        high=max(opening, close) + 0.20,
        low=actual_low,
        close=close,
        volume=10.0,
    )


def _ewo_history() -> list[FiveMinuteBar]:
    closes = [100.0 + 0.02 * index for index in range(209)] + [93.0]
    return [
        _bar(index, close, low=92.5 if index == len(closes) - 1 else None)
        for index, close in enumerate(closes)
    ]


def test_cti_matches_rolling_pearson_extremes() -> None:
    rising = _cti([float(index) for index in range(1, 31)], 20)[-1]
    falling = _cti([float(31 - index) for index in range(1, 31)], 20)[-1]
    assert abs(rising - 1.0) <= 1e-12
    assert abs(falling + 1.0) <= 1e-12


def test_wilder_rsi_extremes_are_bounded() -> None:
    rising = _wilder_rsi([100.0 + index for index in range(30)], 14)[-1]
    falling = _wilder_rsi([130.0 - index for index in range(30)], 14)[-1]
    assert rising == 100.0
    assert falling == 0.0


def test_exact_public_entry_branches_are_separable() -> None:
    ewo = IndicatorSnapshot(
        close=90.0,
        ema8=100.0,
        ema16=100.0,
        ema50=101.0,
        ema200=100.0,
        sma15=100.0,
        rsi_fast=20.0,
        rsi=15.0,
        rsi_slow=18.0,
        prior_rsi_slow=19.0,
        cti=-0.40,
        ewo=1.0,
        fastk=20.0,
    )
    assert e0v1e_branches(ewo) == (True, False, "EWO")

    buy1 = IndicatorSnapshot(
        close=90.0,
        ema8=94.0,
        ema16=100.0,
        ema50=99.0,
        ema200=100.0,
        sma15=100.0,
        rsi_fast=30.0,
        rsi=25.0,
        rsi_slow=22.0,
        prior_rsi_slow=24.0,
        cti=-0.90,
        ewo=-1.0,
        fastk=20.0,
    )
    assert e0v1e_branches(buy1) == (False, True, "BUY_1")

    both = IndicatorSnapshot(
        close=90.0,
        ema8=100.0,
        ema16=100.0,
        ema50=101.0,
        ema200=100.0,
        sma15=100.0,
        rsi_fast=20.0,
        rsi=25.0,
        rsi_slow=22.0,
        prior_rsi_slow=24.0,
        cti=-0.90,
        ewo=1.0,
        fastk=20.0,
    )
    assert e0v1e_branches(both) == (True, True, "BOTH")


def test_synthetic_liquidation_bar_creates_valid_ewo_scenario() -> None:
    bars = _ewo_history()
    state = causal_e0v1e_state(bars, current_ts=bars[-1].ts_event)
    assert state.ready is True
    assert state.raw_entry is True
    assert state.entry is True
    assert state.is_ewo is True
    assert state.branch in {"EWO", "BOTH"}
    assert state.stop < state.close < state.target
    assert state.stop >= state.close * 0.90
    assert math.isfinite(state.reward_risk) and state.reward_risk > 0.0


def test_future_stale_gapped_or_nonfinite_input_is_rejected() -> None:
    bars = _ewo_history()
    for timestamp in (bars[-1].ts_event - STEP, bars[-1].ts_event + STEP):
        try:
            causal_e0v1e_state(bars, current_ts=timestamp)
        except ValueError:
            pass
        else:
            raise AssertionError(f"mismatched timestamp {timestamp} was accepted")

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
        causal_e0v1e_state(gapped, current_ts=gapped[-1].ts_event)
    except ValueError:
        pass
    else:
        raise AssertionError("gapped E0V1E history was accepted")

    invalid = list(bars)
    item = invalid[-1]
    invalid[-1] = FiveMinuteBar(
        ts_event=item.ts_event,
        open=item.open,
        high=item.high,
        low=item.low,
        close=math.nan,
        volume=item.volume,
    )
    try:
        causal_e0v1e_state(invalid, current_ts=invalid[-1].ts_event)
    except ValueError:
        pass
    else:
        raise AssertionError("non-finite E0V1E input was accepted")


def test_future_append_cannot_change_prior_state_when_replayed_causally() -> None:
    bars = _ewo_history()
    prior = causal_e0v1e_state(bars, current_ts=bars[-1].ts_event)
    future = _bar(len(bars), 200.0, low=50.0)
    extended = [*bars, future]
    replay = causal_e0v1e_state(
        extended[:-1],
        current_ts=bars[-1].ts_event,
    )
    assert replay == prior
