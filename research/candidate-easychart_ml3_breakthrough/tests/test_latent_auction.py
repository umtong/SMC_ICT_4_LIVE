from __future__ import annotations

from domain import Side
from latent_auction import AuctionRegime, LatentAuctionState


def test_latent_state_keeps_draw_trend_and_factor_separate() -> None:
    state = LatentAuctionState(
        time_ns=1,
        regime=AuctionRegime.TRANSITION,
        draw_side=Side.LONG,
        draw_balance=0.4,
        trend_60=-0.7,
        trend_15=0.5,
        factor_side=Side.LONG,
        channel_location_long=0.8,
        channel_location_short=-0.8,
    )
    assert state.draw_alignment(Side.LONG) == 1.0
    assert state.draw_alignment(Side.SHORT) == -1.0
    assert state.trend_alignment(Side.LONG, 60) == -0.7
    assert state.trend_alignment(Side.LONG, 15) == 0.5
    assert state.factor_alignment(Side.LONG) == 1.0
    assert state.location_alignment(Side.SHORT) == -0.8


def test_neutral_draw_and_factor_do_not_become_opposition() -> None:
    state = LatentAuctionState(
        time_ns=1,
        regime=AuctionRegime.RANGE,
        draw_side=None,
        draw_balance=0.0,
        trend_60=0.0,
        trend_15=0.0,
        factor_side=None,
        channel_location_long=0.5,
        channel_location_short=-0.5,
    )
    assert state.draw_alignment(Side.LONG) == 0.0
    assert state.factor_alignment(Side.SHORT) == 0.0
