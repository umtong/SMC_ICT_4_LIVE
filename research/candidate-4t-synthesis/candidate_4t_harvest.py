#!/usr/bin/env python3
"""Candidate 4t causal-auction action harvester.

This module deliberately reuses, rather than renames, the strongest implemented
pieces of the existing research lineage:

* candidate 1k: semantic-liquidity interaction, first-return entry, structural
  invalidation, and the exact first still-live opposing-liquidity destination;
* candidate 2c: a pending entry dies only when the causal opportunity is
  invalidated, spent, or passed;
* auction-episode-system: event-time auction states and price/volume response
  descriptors over the whole opportunity.

The new work is in ``candidate_4t_policy.py``. This wrapper makes the above
mechanics the immutable action universe consumed by that policy.
"""
from __future__ import annotations

# candidate_2c_harvest already composes candidate 1k with the auction-episode
# state harvester, then replaces fixed expiry with causal opportunity death.
import candidate_2c_harvest as synthesis

core = synthesis.core
core.POLICY = (
    "CANDIDATE_4T_SYNTHESIS_SEMANTIC_LIQUIDITY_AUCTION_"
    "EVENT_TIME_RESPONSE_CAUSAL_PENDING_EXACT_OPPOSING_ROUTE"
)
core.generate_symbol = synthesis.generate_symbol

if __name__ == "__main__":
    core.main()
