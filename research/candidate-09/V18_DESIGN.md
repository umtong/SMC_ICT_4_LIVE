# Candidate-09 v18 — State-strength-aware failure confirmation

## Evidence that created the hypothesis

The exact v17 experiment changed only the persistence requirement after an accepted
breakout failed. Applying persistence to every path reduced the pooled fixed-week result to
0.5765% geometric growth per day, 12 trades, and two active weeks. The exact v14
single-close control retained 1.3617% per day with 16 trades. The path-specific result was
not symmetric:

- persistence only after `RETESTED`: 1.3877% per day, 15 trades, all three weeks active,
  maximum single-winner share 24.49%;
- persistence only directly from `ACCEPTED`: 0.4050% per day and 14 trades.

This is interpreted as a state-strength result, not a return-tuned threshold. An outside
auction in `RETESTED` has already survived a completed retest and therefore has stronger
confirmation than a direct `ACCEPTED` state. One contrary bar can invalidate the weaker
un-defended acceptance, but invalidating the defended state requires persistent internal
reacceptance.

## Frozen v18 sequence

- Direct `ACCEPTED` failure: exact v14 opposite displacement, flow, and inside-close
  contract; enter on the first completed failure close with the exact failed-boundary stop.
- `RETESTED` failure: exact v17 pending state; the next completed close must remain inside;
  enter on the second close with invalidation beyond the boundary and both confirmation
  bars.
- Nonpersistent second close: restore `RETESTED` and process the same completed bar under
  the restored state.

## Exact controls

| Variant | Meaning |
|---|---|
| `baseline` | Single close from `ACCEPTED`; two closes from `RETESTED` |
| `single-close` | Exact v14 on both paths |
| `two-close-all` | Exact v17 baseline on both paths |
| `direct-only` | Opposite asymmetry: two closes from `ACCEPTED`, one from `RETESTED` |

No market detector, numerical threshold, target, cost, risk fraction, data interval, or
execution rule changes. The same pooled gate is rerun and only a passing v18 baseline may
advance to the frozen 2022-01-01 through 2025-01-01 BTC evaluation.
