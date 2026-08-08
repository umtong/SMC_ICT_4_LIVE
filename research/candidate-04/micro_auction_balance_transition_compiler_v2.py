#!/usr/bin/env python3
"""V57 implementation repair: align a 30-bar balance with its 29 internal transitions.

The original balance metrics requested 30 price differences for 30 completed
bars.  A 30-bar auction contains 29 internal close-to-close transitions, so the
first otherwise valid completed balance produced a NaN path and could not be
frozen.  This module changes only that alignment.  All market-state thresholds,
break routes, outcomes, stops, targets and NautilusTrader execution remain
unchanged.
"""
from __future__ import annotations

import pandas as pd

import micro_auction_balance_transition_compiler as base


BALANCE_BARS = base.BALANCE_BARS


def build_balance_metrics(data: pd.DataFrame) -> base.BalanceMetrics:
    high = data["high"].astype(float).rolling(
        BALANCE_BARS,
        min_periods=BALANCE_BARS,
    ).max()
    low = data["low"].astype(float).rolling(
        BALANCE_BARS,
        min_periods=BALANCE_BARS,
    ).min()
    width = high - low
    atr = data["atr"].astype(float)
    width_atr = width / atr.replace(0.0, float("nan"))
    close = data["close"].astype(float)

    # A window of N completed bars contains exactly N-1 internal transitions.
    transition_count = BALANCE_BARS - 1
    path = close.diff().abs().rolling(
        transition_count,
        min_periods=transition_count,
    ).sum()
    path_to_width = path / width.replace(0.0, float("nan"))
    net = (close - close.shift(transition_count)).abs()
    net_efficiency = net / path.replace(0.0, float("nan"))

    oi = data["metric_sum_open_interest"].astype(float)
    oi_mean = oi.rolling(BALANCE_BARS, min_periods=BALANCE_BARS).mean()
    oi_dispersion = (
        oi.rolling(BALANCE_BARS, min_periods=BALANCE_BARS).max()
        - oi.rolling(BALANCE_BARS, min_periods=BALANCE_BARS).min()
    ) / oi_mean.replace(0.0, float("nan"))
    close_location = (close - low) / width.replace(0.0, float("nan"))
    return base.BalanceMetrics(
        high=high,
        low=low,
        width_atr=width_atr,
        path_to_width=path_to_width,
        net_efficiency=net_efficiency,
        oi_dispersion=oi_dispersion,
        close_location=close_location,
    )


# collect_signals resolves this name in the original module at call time.
base.build_balance_metrics = build_balance_metrics


# Re-export the causal types and helpers used by regression tests.
BalanceMetrics = base.BalanceMetrics
Thresholds = base.Thresholds
FrozenBalance = base.FrozenBalance
BreakState = base.BreakState
shifted_quantile = base.shifted_quantile
freeze_balance = base.freeze_balance
balance_qualifies = base.balance_qualifies
classify_inventory_route = base.classify_inventory_route
boundary_reentered = base.boundary_reentered
boundary_retest_holds = base.boundary_retest_holds
excursion_stop = base.excursion_stop
collect_signals = base.collect_signals


if __name__ == "__main__":
    base.v22.main()
