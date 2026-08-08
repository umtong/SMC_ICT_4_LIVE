# Candidate 14 v8 — Explicit Accepted-Auction Failure

## Evidence carried forward

Candidate 14 L1 failed because ordinary FAR could relabel mixed acceptance/rejection events on the same completed bar. V6 fixed the categorical ownership defect and changed the 84-day account from 80,737.94 to 100,407.07 USDT, but supplied only eight trades.

V7 tested whether the already-observed AAC pullback/reacceleration leg should enter immediately rather than wait at the old defended pivot. Its two AAC market entries both lost. Final NAV improved to 103,561.84 USDT only because four AAC instructions became no-trades; AAC itself did not acquire positive expectancy.

## V8 state model

The incomplete AAC continuation label is no longer traded. Acceptance-origin events resolve as follows:

```text
outside acceptance origin
→ existing deep boundary re-entry condition
→ AAC_FAILURE_OBSERVED
→ same completed bar cannot reverse
→ later completed opposite initiative breaks failure-bar extreme
→ inherited body / flow / close-location displacement conditions
→ failure-bar extreme invalidation
→ still-live opposing external draw
→ exact costed 3% NAV sizing and Nautilus execution
```

If acceptance restores its prior extreme, the failure state is rescinded. If the opposing target is consumed or expires, the state terminates. If the later leg lacks costed structural R, it terminates rather than falling back to a different entry.

Exclusive rejection-origin FAR, Session I7, cross-market semantics, fees, slippage reserve, source liquidity identity, target hierarchy, one global slot and current-NAV risk sizing remain unchanged.

## Evidence role

The 2026-05-11 through 2026-08-03 interval has already been inspected. V8 uses it only for controlled mechanism diagnosis. It cannot establish project success under any result. A surviving state machine must be frozen before collecting a new continuous interval.
