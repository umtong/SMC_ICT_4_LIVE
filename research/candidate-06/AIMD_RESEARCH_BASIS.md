# Candidate 06 AIMD Research Basis

## Market mechanism

The candidate treats a volume profile as a completed auction, not as an entry
pattern.  A migrated POC and value area indicate that traded inventory has been
accepted at new prices.  Directional efficiency and aggressive delta distinguish
that state from a broad balanced distribution.  The following pullback tests
whether opposing activity can restore the old accepted value.  Failure to do so,
followed by separate directional resumption, is the tradable scenario.

## Research carried forward

BAVR showed that a balanced-value filter removed many false reversions, while
fourteen of twenty-five qualified value-edge excursions were accepted as price
discovery.  The no-balance reversion ablation had ten trades but negative
cost-after expectancy.  AIMD therefore trades the complementary causal branch
rather than relaxing BAVR.

## Literature mapping

- Continuous double-auction research motivates treating observed prices as the
  outcome of a dynamic order-submission and matching process rather than fixed
  candle shapes.
- Order-flow imbalance and price-impact work motivates requiring both signed
  aggression and realized directional displacement.
- Limit-order-book price-discovery work motivates distinguishing aggressive
  price discovery from failed exploration.
- Auction-market and volume-profile practice motivates POC/value migration as a
  representation of where inventory was actually accepted.

These sources motivate variables and state order only.  They do not prove
profitability and their numeric examples are not imported as optimized
thresholds.

## SMC/ICT translation

- displacement: completed auction moves value and closes beyond prior value;
- order-flow confirmation: aggregate-trade delta aligns with migration;
- fair-value mitigation: first retest of the migrated value edge;
- market-structure confirmation: a later completed minute breaks retest
  structure in the discovery direction;
- draw on liquidity: completed migration-auction high/low or explicit value
  extension;
- invalidation: acceptance back inside old value.

The detector and trading scenario remain separate.  Human chart discretion is
not part of the execution path.
