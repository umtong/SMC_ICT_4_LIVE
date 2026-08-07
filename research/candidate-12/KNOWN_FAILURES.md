# Known failure conditions

A positive headline return is insufficient if any condition below applies.

1. W1 fails its minimum closed-trade, win-rate, payoff, post-cost geometric-growth, liquidation, risk-budget, global-slot, or event-chronology gate. W2/W3 must not run in that state.
2. A current, unfinished session high or low is used as if it were a completed liquidity range.
3. A range touch, wick, FVG, MSS, or session timestamp is treated as a standalone entry rather than one ordered scenario.
4. A pre-window break is mislabeled as a fresh sweep. It must be classified from completed outside closes and later acceptance or re-entry.
5. Acceptance is chased without a completed pullback/retest and reacceleration.
6. Failed-auction invalidation is placed inside the causal sweep or pullback extreme.
7. The target is an arbitrary fixed multiple rather than a pre-existing session, prior-day, or measured-auction objective with positive costed structural R.
8. One-minute taker-buy volume is claimed to be full order-book imbalance or quote replenishment evidence. It is only a completed trade-flow proxy.
9. Bar replay outcome depends materially on unknown intrabar high/low ordering. Such a result requires higher-resolution validation.
10. Effective fee/slippage/funding reserves understate actual execution, or large NAV is assumed scalable without depth/participation validation.
