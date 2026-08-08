"""Candidate 14 v6 auction-origin ownership.

A failed-auction reversal may use the ordinary reclaim -> MSS -> displacement
transition only when the triggering liquidity event began as an exclusively
rejection-framed auction.  If the same event also seeded accepted-auction
continuation, a later reversal requires a separate explicit acceptance-failure
state; the generic FAR transition is not allowed to silently relabel it.

This module adds no magnitude threshold, symbol/session whitelist, risk
multiplier, order simulator, or PnL logic.  It changes one categorical state
transition and delegates every valid rejection-origin FAR to the preserved
Candidate 14 implementation.
"""
from __future__ import annotations

from logic import Auction, BarObs, CausalAuctionEngine, TradePlan


BASE_CONFIRM_FAR = CausalAuctionEngine._confirm_far


def far_origin_is_exclusive_rejection(auction: Auction) -> bool:
    """Return whether ordinary FAR owns the causal origin of this auction."""
    return bool(auction.rejection_seed and not auction.acceptance_seed)


def confirm_far_from_owned_origin(
    self: CausalAuctionEngine,
    auction: Auction,
    bar: BarObs,
) -> TradePlan | None:
    """Suppress ambiguous/acceptance-origin relabeling; preserve valid FAR."""
    if not far_origin_is_exclusive_rejection(auction):
        return None
    return BASE_CONFIRM_FAR(self, auction, bar)


def install() -> None:
    """Install the single v6 scenario-transition change."""
    CausalAuctionEngine._confirm_far = confirm_far_from_owned_origin
