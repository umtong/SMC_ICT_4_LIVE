# Known failure and invalidation conditions

These conditions are not excuses for a failed test. They define where the causal claim is expected to break or where the current evidence becomes insufficient for deployment.

## Strategy-logic failure

1. **One-sided repricing without retest.** A genuine information shock can leave a completed range permanently. The rejection branch is invalidated; the acceptance branch may never receive a safe retest and must miss the move.
2. **Repeated boundary churn.** High/low crossings inside a broad noisy bar sequence can create ambiguous auctions. The state machine permits one completed trade per block and expires stale states, so it may remain inactive.
3. **Weak internal structure.** A trailing high/low is a causal proxy, not proof of institutional structure. In strongly overlapping markets, a break can be statistically ordinary.
4. **Flow-price divergence.** Binance kline taker-buy quote volume is an aggregate trade-side proxy. It cannot reveal hidden liquidity, cancellations, queue depletion, cross-venue leadership, or liquidation origin.
5. **Regime-dependent auction duration.** Four hours may cease to be an economically useful range horizon. The fixed horizon avoids optimization, but can also become wrong.
6. **Range target unavailable.** After a late retrace, opposing liquidity can be too close after costs. The adapter rejects the delayed plan rather than force a low-quality trade.

## Data and simulation failure

1. **Missing or discontinuous minutes.** The loader hard-fails a gap larger than 61 seconds. A run with a gap is invalid.
2. **One-minute path ambiguity.** OHLC does not reveal the exact high/low order. Adaptive bar ordering is still a heuristic. Any segment where both stop and target lie inside the same bar requires lower-granularity replay before live approval.
3. **No depth or market impact curve.** A fixed 7 bps per-side stress cannot prove fill capacity for very large NAV. Effective leverage and quantity are reported, but depth-aware replay is required before scaling.
4. **Funding approximation.** Funding is folded into the composite cost rather than replayed from historical funding updates. A strategy that systematically crosses abnormal funding windows can be overstated.
5. **Contract metadata drift.** Tick, lot, margin tier, and leverage bracket can change. The BTC research instrument uses declared metadata and engine liquidation, but a live adapter must load current venue metadata and reject an invalid order.
6. **Report-marker limitation.** `liquidation_marker_rows` scans engine reports for explicit liquidation text. The stronger safeguards are engine liquidation being enabled and the recorded equity-to-maintenance-margin ratio; neither replaces live exchange reconciliation.

## Execution and operational failure

1. **Protective order denied or rejected.** The global gate remains occupied and the adapter immediately requests a reduce-only flatten. Any such event fails the candidate completion gate.
2. **Entry acknowledged without protection.** This is a live emergency condition. New entries remain blocked until exchange reconciliation confirms the position is flat or protected.
3. **Stale or out-of-order bar.** Core processing raises on non-monotonic event time. Trading must halt and reconcile rather than reorder signals silently.
4. **Global gate split brain.** Across BTC, ETH, SOL, and XRP, the pending-entry/position owner must be persisted and reconciled after restart. Two owners invalidate the system.
5. **Exchange liquidation or margin breach.** No strategy-level leverage cap is added, but a venue rejection or liquidation is a hard failure, not an acceptable way to enforce risk.
6. **Clock boundary mismatch.** A live feed that timestamps bars at open rather than close would introduce look-ahead. The adapter must emit an `AuctionBar` only after the minute is complete.

## Research invalidation

1. **Changing parameters after inspecting confirmation weeks.** Those weeks cease to be confirmation data. A new frozen protocol and version are required.
2. **Selecting only profitable symbols or periods.** BTC is tested first by protocol. Other allowed symbols are experimental venues, not a rescue optimization set.
3. **Removing fees, delay, failed fills, or losing days.** Any such run is incomparable to the declared result.
4. **Fewer than the declared opportunities.** A high return from a handful of trades cannot satisfy `candidate_success`.
5. **Profit concentration.** Even if the mechanical gate passes, live approval requires trade-level concentration analysis; one or two outliers dominating annual PnL is inconsistent with the project objective.
6. **Structural decay.** Rolling live shadow results that fall outside the historical scenario-conditioned distribution require suspension and new research, not automatic risk reduction disguised as a fix.
