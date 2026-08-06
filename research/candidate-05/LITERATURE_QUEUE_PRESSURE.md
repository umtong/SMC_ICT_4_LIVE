# Candidate 05 — Queue Pressure and Confirmed Release

Candidate 05 v32 is motivated by primary limit-order-book research, but the
papers are not treated as evidence of trading profitability.  They motivate a
causal distinction between displayed pressure and pressure which becomes actual
trading and price discovery.

## Primary sources

1. Rama Cont and Adrien de Larrard, **Price Dynamics in a Markovian Limit Order
   Market**, *SIAM Journal on Financial Mathematics* (2013), arXiv:1104.4596.
   The state of the best bid and ask queues affects short-horizon price-move
   probabilities.  Candidate 05 translates this into a directional depth-
   imbalance observation, not an immediate trade.

2. Alexander Lipton, Umberto Pesavento and Michael Sotiropoulos, **Trade Arrival
   Dynamics and Quote Imbalance in a Limit Order Book** (2013), arXiv:1312.0563.
   Quote imbalance contains information about subsequent trade arrivals and
   short-horizon direction.  Candidate 05 requires persistence across completed
   observations and then independent confirmation by aggressive flow.

3. Rama Cont, Arseniy Kukanov and Sasha Stoikov, **The Price Impact of Order Book
   Events**, *Journal of Financial Econometrics* (2014), arXiv:1011.6402.
   Order-flow imbalance and available depth jointly explain short-horizon price
   impact.  Candidate 05 therefore rejects a displayed-queue signal unless
   signed flow, efficiency and threatened-side depth withdrawal confirm it.

## Testable translation

1. Three completed one-minute observations hold a mirror-symmetric two-to-one
   directional top-depth imbalance while price remains inside a narrow range.
2. A later completed bar closes beyond that range in the same direction with
   the existing Candidate 05 notional-burst, signed-flow, efficiency, close-
   location and opposing-depth-withdrawal acceptance contract.
3. The target and invalidation are frozen at breakout time.  The target must be
   a still-live opposing liquidity pool; the stop lies beyond the opposite side
   of the compression plus the existing ATR buffer.
4. No same-bar fill is permitted.  A passive limit can be submitted only after
   the first later completed retest touches the broken boundary, closes outside
   it and retains current tail-flow and depth support.
5. The v26 system is the exact branch-removal control.  Thresholds, costs, 3%
   current-NAV risk sizing and NautilusTrader execution are unchanged.

This is intentionally distinct from the external-level continuation and
impact-resiliency families.  It does not require a prior liquidity sweep or an
external session extreme; it tests whether latent queue pressure becomes actual
auction expansion.
