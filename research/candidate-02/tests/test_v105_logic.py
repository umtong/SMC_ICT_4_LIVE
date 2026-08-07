"""Focused causal-state tests for candidate-02 v105."""
from __future__ import annotations

import pandas as pd

from v105_auction_state_core import (
    AuctionStateConfig,
    _classify_common_auction,
    _find_directional_fvg,
    _latest_confirmed_internal_pivot,
)


def _index(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2025-01-01T00:01:00Z", periods=n, freq="min")


def test_acceptance_impulse_fvg_is_not_forced_to_occur_after_acceptance() -> None:
    frame = pd.DataFrame(
        {
            "raw_open": [99.5, 100.0, 100.0],
            "raw_high": [100.0, 100.4, 102.2],
            "raw_low": [99.0, 99.8, 100.6],
            "raw_close": [99.8, 100.1, 102.0],
            "body": [0.3, 0.1, 2.0],
            "body_threshold": [0.2, 0.2, 0.2],
            "atr": [1.0, 1.0, 1.0],
        },
        index=_index(3),
    )
    event = _find_directional_fvg(
        x=frame,
        first_position=2,
        last_position=2,
        boundary=101.0,
        direction=1,
        config=AuctionStateConfig(),
    )
    assert event is not None
    assert event.position == 2
    assert event.fvg_high > event.fvg_low


def test_common_acceptance_and_failed_auction_are_mutually_exclusive() -> None:
    config = AuctionStateConfig()
    previous = pd.Series({"perp_spot_log_basis": 0.0})
    accepted = pd.DataFrame(
        {
            "raw_close": [101.2, 101.4, 101.3],
            "spot_close": [101.2, 101.4, 101.3],
            "perp_spot_log_basis": [0.0, 0.0, 0.0],
        },
        index=_index(3),
    )
    accepted_result = _classify_common_auction(
        x=accepted,
        previous=previous,
        event_position=0,
        classification_end=2,
        boundary=101.0,
        direction=1,
        atr_value=1.0,
        config=config,
    )
    assert accepted_result.state == "ACCEPTED_AUCTION"
    assert accepted_result.reentry_position is None

    failed = pd.DataFrame(
        {
            "raw_close": [101.2, 100.8, 100.6, 100.5],
            "spot_close": [101.1, 100.7, 100.6, 100.5],
            "perp_spot_log_basis": [0.0, 0.0, 0.0, 0.0],
        },
        index=_index(4),
    )
    failed_result = _classify_common_auction(
        x=failed,
        previous=previous,
        event_position=0,
        classification_end=2,
        boundary=101.0,
        direction=1,
        atr_value=1.0,
        config=config,
    )
    assert failed_result.state == "FAILED_AUCTION"
    assert failed_result.reentry_position == 1


def test_mss_pivot_must_be_confirmed_before_the_liquidity_breach() -> None:
    lows = [12.0, 11.0, 10.0, 9.0, 8.0, 9.0, 10.0, 9.5, 9.0, 8.5]
    highs = [value + 2.0 for value in lows]
    frame = pd.DataFrame({"raw_low": lows, "raw_high": highs}, index=_index(len(lows)))
    pivot = _latest_confirmed_internal_pivot(
        x=frame,
        event_position=8,
        breakout_direction=1,
        config=AuctionStateConfig(mss_pivot_radius=2),
    )
    assert pivot is not None
    assert pivot[0] == 8.0
    assert pivot[1] == int(frame.index[6].value)


def test_reversal_displacement_must_break_the_prebreach_pivot() -> None:
    frame = pd.DataFrame(
        {
            "raw_open": [102.0, 101.5, 101.0],
            "raw_high": [102.2, 101.8, 101.1],
            "raw_low": [101.8, 101.2, 98.8],
            "raw_close": [102.0, 101.3, 99.0],
            "body": [0.0, 0.2, 2.0],
            "body_threshold": [0.1, 0.1, 0.1],
            "atr": [1.0, 1.0, 1.0],
        },
        index=_index(3),
    )
    no_break = _find_directional_fvg(
        x=frame,
        first_position=2,
        last_position=2,
        boundary=101.0,
        direction=-1,
        must_break=98.5,
        config=AuctionStateConfig(),
    )
    assert no_break is None
    broken = _find_directional_fvg(
        x=frame,
        first_position=2,
        last_position=2,
        boundary=101.0,
        direction=-1,
        must_break=99.5,
        config=AuctionStateConfig(),
    )
    assert broken is not None
