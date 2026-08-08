# Candidate 14 v9 — Confirmed Accepted-Auction Failure

## Evidence carried forward

- Original Candidate 14: 84 continuous days, 15 trades, 3 wins and 12 losses, final NAV 80,737.94 USDT.
- V6 exclusive rejection-origin FAR: removed seven mixed-origin FAR losses; 8 trades, 3 wins and 5 losses, final NAV 100,407.07 USDT.
- V7 AAC confirmation-time entry: two AAC entries, both losses; improvement came from no-trades, not positive AAC expectancy.
- First V8 attempt: 128 pseudo-failure reversals because a possible acceptance was treated as a completed acceptance. Matching scenarios would not complete the frozen AAC sequence until 43–247 minutes later.

## V9 causal state

```text
outside acceptance possibility
→ frozen minimum outside holds
→ causal defended pullback pivot
→ later reacceleration beyond frozen impulse extreme
→ ACCEPTANCE_COMPLETION_OBSERVED, no continuation order
→ later deep source-boundary re-entry
→ CONFIRMED_ACCEPTANCE_FAILURE_OBSERVED, no same-bar reversal
→ another later opposite initiative through failure-bar extreme
→ failure-bar invalidation
→ still-live opposing external draw
→ exact costed 3% NAV sizing and Nautilus execution
```

Before completion, re-entry is merely an unsuccessful acceptance attempt and cannot reverse. After completion, if price restores the accepted extreme, the failure substate is rescinded. If the opposing draw is consumed or expires, the episode terminates. The parent auction remains `OBSERVE` until a later initiative actually resolves it.

All detector magnitudes, cross-market semantics, liquidity identities, external targets, costs, Session I7, one global slot and current-NAV sizing remain unchanged.

## Evidence role

The 2026-05-11 through 2026-08-03 interval is already inspected. V9 uses it only for controlled mechanism diagnosis and can never establish project success. A surviving system must be frozen before a newly reserved continuous interval is collected.
