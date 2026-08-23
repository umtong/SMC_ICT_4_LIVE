# Candidate 3b — proof-route completion synthesis

This candidate is one integrated causal liquidity-auction policy for BTCUSDT,
ETHUSDT, SOLUSDT and XRPUSDT. It is not an OB strategy plus an FVG strategy plus a
channel strategy. Semantic liquidity owns direction; market structure defines the
auction and invalidation; OB/FVG/boundary geometry refines the first-return entry; and
the same four instruments compete for one account and one position.

## Research synthesis

The implementation reuses, rather than rediscovers, the major lineage:

- semantic liquidity ownership, event identity and shared failed/accepted grammar;
- event-lifetime arm states and price/volume effort-result measurements;
- causal first-return limit entries, structural stops and opposing-liquidity route;
- candidate 2c pending-order cancellation without arbitrary response-bar exits;
- one-account arbitration and continuous 3% risk NAV accounting.

## Missing piece: proof-route coupling

Earlier exact-route candidates forced the nearest opposing structural obstacle to be the
full take-profit. That obstacle can be many risk units away, so an otherwise correct
first-return entry was judged against a payoff the observed response had not proved it
could complete.

Candidate 3b separates two roles:

1. the first opposing obstacle is the **structural runway ceiling**;
2. the already observed directional delivery is the **proof leg** from which an
   immutable first-return completion target is derived.

The policy arms only accepted auctions and uses two expressions of the same principle:

- **Proven delivery / deep route:** the completed response has delivered at least 1.75R
  from the entry zone and a 1.5R completion target occupies no more than the first 20%
  of the still-live structural route;
- **Confluent first return:** a 1.2R delivery is already observed, the first retest is
  forming at a liquidity-boundary plus FVG/OB execution footprint, and directional
  price result is efficient relative to observed activity/flow.

A target is retained only when at least one third of its nominal reward and at least
0.30R remain after the inherited maker/taker fees and stop slippage. This is execution
viability, not a substitute for direction logic.

A previous visit to the completion target does not cancel the pending order: that visit
is the causal proof for a later revisit. The pending order still cancels on event
invalidation, a passed first return, or causal opportunity expiry. After fill, the only
exits are the predeclared TP and SL; same-minute ambiguity is resolved against the
strategy.

## Account policy

- one global account and at most one live order/position across all four symbols;
- no scale-in or scale-out;
- each closed trade changes continuous NAV by `1 + 0.03 * net_R`;
- no daily loss cap or arbitrary time liquidation;
- first qualifying completed state of an episode acts; later evidence cannot be used to
  wait retrospectively;
- simultaneous opportunities are resolved by causal scenario strength, executable
  target economics, delivered proof and structural runway.

## Reproduction

The workflow `.github/workflows/research-candidate-3b-short.yml` runs causal harvests
for four previously inspected and four untouched seven-day windows, routes all actions
through one account, and uploads the exact actions, orders, trades and continuous NAV.
