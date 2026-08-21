# Causal Liquidity Route System

This branch does not tune the previous plan/RR lattice. It replaces the decision unit.

A trade must be one causal route:

1. a causally confirmed horizontal liquidity pool or projected diagonal boundary is interacted with;
2. the auction terminates as either `FAILED_AUCTION_REVERSAL` or `ACCEPTED_AUCTION_CONTINUATION`;
3. event-volume, price efficiency, signed flow and reclaim/hold geometry confirm who controls the auction;
4. FVG, order block and S/R-flip geometry define the first defended return rather than vote on direction;
5. the entire position enters once, with a predeclared structural stop and the nearest still-live opposing liquidity as the target;
6. gross planned RR must remain at least 1.0 after the executable next-open entry;
7. a filled position ends only at its target or stop;
8. BTC, ETH, SOL and XRP compete for one global position, and each completed trade risks 3% of then-current NAV.

The short workflow uses scattered development windows and later untouched windows only to expose implementation and market-logic errors quickly. It emits actual trades, losing cases, source events that produced no trade, and case charts. It does not train a selector or implement a pass/fail promotion system.
