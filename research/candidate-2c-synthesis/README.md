# Candidate 2c — research synthesis and the missing decision layer

Candidate 2c is not a threshold revision of an earlier policy. It preserves the causal
and accounting machinery that is already useful, but changes the decision architecture.

The reusable synthesis is:

1. pre-existing semantic liquidity and market structure define the interaction;
2. failed-auction reversal and accepted-auction continuation are two resolutions of the
   same auction event;
3. price, volume, response efficiency, basis, OI, breadth and common-market behavior
   describe who owns the auction;
4. OB, FVG and S/R-flip geometry refine the first-return price only after direction;
5. the event extreme is the invalidation and the first unconsumed opposing liquidity or
   causal volume obstacle is the full-position target;
6. plans below 1R gross are absent, and all costs remain in the realized label;
7. BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT compete for one continuous account slot at 3%
   fixed NAV risk.

## Missing piece: sequential auction ownership belief

Earlier episode systems scored each event-time state almost independently and mixed
auction direction with entry geometry. A skilled trader instead updates a directional
thesis as evidence arrives, recognizes when a contradictory response invalidates stale
evidence, and then chooses price.

Candidate 2c factorizes those layers. An action-independent ownership model observes the
causal auction state. A persistent Bayesian filter accumulates observations through the
whole episode. The filter continuously estimates a change-point probability from abrupt
contradictions and resets obsolete belief toward the empirical prior rather than relying
on a fixed bar window. Each exact entry/stop/target action is then priced with that shared
posterior. The first posterior state whose realistic expected log growth beats cash may
arm; no extra score threshold is imposed.

Pending limits end only when the event is invalidated, the objective is spent, or the
first return actually passes the declared entry. Filled trades end only at the immutable
TP or SL. The short research workflow exists to expose implementation and market-logic
errors; it is not long-run evidence.
