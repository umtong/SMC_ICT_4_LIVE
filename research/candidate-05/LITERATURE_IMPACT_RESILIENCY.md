# Candidate 05 — Impact and Resiliency Research Basis

This note records the primary market-microstructure mechanisms used to formulate
Candidate 05 v31.  It is not a claim that any paper guarantees trading alpha.
The papers motivate causal observations which are then tested, with costs and
execution, in NautilusTrader.

## Primary sources

1. Rama Cont, Arseniy Kukanov and Sasha Stoikov, **The Price Impact of Order Book
   Events**, *Journal of Financial Econometrics* (2014), arXiv:1011.6402.
   The paper relates short-horizon price changes to order-flow imbalance and
   available depth.  Candidate 05 uses this as motivation to distinguish a real
   directional liquidity shock from a mere price crossing.

2. Jeremy Large, **Measuring the Resiliency of an Electronic Limit Order Book**,
   *Journal of Financial Economics* (2007).  The paper treats the replenishment
   of liquidity following aggressive order flow as an observable dynamic rather
   than assuming static depth.  Candidate 05 uses current same-side refill and
   the decay of price efficiency to test whether impact was absorbed.

3. Anna Obizhaeva and Jiang Wang, **Optimal Trading Strategy and Supply/Demand
   Dynamics**, *Journal of Financial Economics* (2013).  Its transient-impact
   framework motivates separating persistent price discovery from temporary
   displacement which decays as the book recovers.

## Testable translation

The v31 hypothesis is deliberately narrower than the literature:

1. A fully completed four-hour UTC activity session creates an external high or
   low.  The level is unavailable until the session is complete.
2. The level receives a material penetration with the existing Candidate 05
   notional-burst, signed-flow, price-efficiency and threatened-side depth-
   withdrawal acceptance contract.
3. A later completed bar shows impact failure, not merely a pause:
   price closes back through the external level and the shock midpoint, marginal
   price efficiency collapses, tail flow turns against the shock and the
   depleted side of the book refills.
4. The strategy does not enter retrospectively.  It waits for the first later
   return to the failed external level and requires that completed retest to be
   defended by current tail flow and resting depth in the reversal direction.
5. The stop is beyond the original shock extreme plus the existing ATR buffer.
   The target must be a still-live opposing liquidity pool.  No arbitrary R
   fallback is accepted for this branch.

## Controlled interpretation

- The v26 strategy is the exact branch-removal control.
- If v31 cannot create executable trades, the opportunity path is too sparse.
- If its branch trades but has non-positive cost-after PnL, the impact-resiliency
  reversal hypothesis fails on the frozen week.
- If it improves Week 1, it must repeat over two additional frozen weeks before
  a continuous 30-day run.
- A 91-day run is permitted only after the whole fixed 30-day cost-after NAV
  geometric daily growth reaches at least 1% with integrity checks passing.
- No detector threshold, cost, fill model, risk fraction, leverage rule or
  portfolio-accounting rule is optimized in this experiment.
