# Candidate-09 v23 — OI liquidation-cascade continuation

## Why the direction changed

v22 detected 134 abnormal completed OI-reduction pulses and 66 failed-progress states on
the frozen weeks, but every reversal confirmation left non-positive reward after the
unchanged cost model. The positioning detector worked; the assumption that forced
position reduction should immediately mean-revert did not.

## Frozen causal sequence

1. Observe a completed five-minute Binance UM OI update only after its one-minute causal delay.
2. Compare the OI change with the prior 24 completed changes. Baseline requires a drop larger
   than both 5 bp and twice the prior median absolute change.
3. Reconstruct that completed five-minute pulse only from one-minute klines available at the
   metric observation time. Require at least 0.5 ATR displacement, above-median participation,
   aggregate kline taker flow aligned with price, and a close beyond the preceding completed
   fifteen-minute auction edge.
4. Do not enter on the metric-availability close. The next completed one-minute bar alone must
   extend beyond the observed pulse extreme with aligned flow and displacement.
5. Enter in the extension direction. Invalidate beyond the opposite extreme of that persistence
   bar. Target one frozen source-auction width beyond the observed pulse extreme.
6. Apply the unchanged full-cost 1.2R gate and the full-NAV 3% planned-loss sizing contract.

## Exact controls

- `no-oi`: identical price/flow pulse on every completed metric update, irrespective of OI change.
- `oi-rise`: identical magnitude rule with OI increasing rather than decreasing.
- `no-persistence`: enter on the metric-availability close, removing only next-bar persistence.

No threshold search, period selection, risk scaling, or target fitting is performed.

## Independent external support and limitation

Recent cascade studies document futures-led price discovery, abrupt volume expansion, mark-price
feedback, and severe event heterogeneity. These support treating forced-position reduction as a
state that can propagate, but not as a universal continuation signal. OI reduction is still a
position-count aggregate, not a trader-level liquidation label, so the exact OI-sign controls are
mandatory.
