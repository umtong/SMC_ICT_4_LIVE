# Known failure conditions and invalidation criteria

These are not excuses to reinterpret a losing result. They are conditions under which the strategy
logic is expected to fail or the present evidence is insufficient for deployment.

## Structural strategy failures

1. **Two-sided expansion in one bar.** When both external pools trade in the same minute, the bar
   cannot establish event order. The state is `UNRESOLVED` and no entry is allowed.
2. **Persistent auction rather than sweep.** Penetration beyond 1.8 ATR or repeated closes on both
   sides of a level means the pool did not create a clean rejection/acceptance boundary.
3. **Rejection without opposite displacement.** A wick/reclaim that cannot close through the
   interaction midpoint within three bars is cancelled.
4. **Acceptance without held retest.** A displacement that does not retest in ten bars, or closes
   more than 0.38 ATR back through the level, is cancelled.
5. **No external payoff path.** A confirmed direction with no cost-after 1.20 target geometry is
   skipped rather than rescued with a nearer arbitrary target.
6. **Chop-generated swing density.** Rapid alternating pivots can create many nominal pools that are
   not independent liquidity episodes. Concentrated losses and bilateral interactions diagnose this
   regime.
7. **Gap/jump through stop.** The 3% quantity calculation is a plan, not a guarantee. A stop-market
   fill can exceed it during discontinuous moves; actual max loss must be inspected in positions.

## Current evidence limitations

1. **One-minute bar path ambiguity.** Adaptive high/low ordering is more conservative than a fixed
   path but cannot recover true tick order when target and stop are inside one bar.
2. **No historical queue/depth model.** Total volume and candle acceptance are coarse proxies. The
   implementation does not yet observe OFI, book depletion, replenishment, or queue position.
3. **Sub-minute latency is unidentifiable.** Any positive latency on one-minute event data becomes an
   artificial full-bar delay. The baseline uses zero event latency plus adverse tick/slippage and
   fee reserve; tick replay is required before live promotion.
4. **Funding is avoided, not replayed.** The entry blackout plus 180-minute timeout is designed to
   avoid the 8-hour funding boundaries. A real funding-rate stream is still required for exact live
   parity.
5. **Venue leverage tiers/capacity.** The engine uses a single venue leverage value. Real exchange
   maintenance tiers and market impact at very large compounded NAV are not modeled. Capacity is a
   live-deployment invalidation, not a reason to impose an arbitrary research nominal cap.
6. **Only BTC is the first proving ground.** No claim of cross-symbol generality exists until the
   exact state logic transfers without symbol-specific tuning to ETH, SOL, and XRP under the global
   one-position scheduler.

## Promotion failure

The candidate must not advance to the long run when any predeclared screen week is cost-after
negative, has fewer than eight closed trades, has execution failures/residual exposure, or when
profit is dominated by one trade beyond the fixed concentration gate. A long result below 1% daily
geometric NAV growth does not become success through parameter reinterpretation; the branch must
record the failed mechanism and either make one economically motivated revision across all fixed
weeks or stop the candidate.
