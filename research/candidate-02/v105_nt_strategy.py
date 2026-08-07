"""NautilusTrader activation adapter for candidate-02 v105."""
from __future__ import annotations

from v104_nt_strategy import V104ExternalLiquidityStrategy


class V105AuctionStateStrategy(V104ExternalLiquidityStrategy):
    """Use the proven v104 activation guard for both v105 auction branches.

    Both continuation and failed-auction reversal deliberately preserve the
    same boundary/stop/target geometry contract, one-minute delayed activation,
    exchange-rounded prices, current-NAV 3% planned-loss sizing, and full
    activation-bar invalidation checks.
    """


__all__ = ["V105AuctionStateStrategy"]
