# Candidate 14 v7 failure analysis

## Decision

`candidate-14-v7-owned-aac-reacceleration` is rejected as a complete candidate and its AAC market-entry mechanism is not retained.

The strategy was evaluated from `2026-05-11` through `2026-08-03` in one continuous NautilusTrader account with no weekly reset.

- final NAV: `103,561.84321016 USDT`
- daily geometric growth: `+0.041674%`
- closed trades: `7`
- wins / losses: `3 / 4`
- win rate: `42.8571%`
- continuous realized drawdown: `8.7754%`
- active calendar weeks: `5 / 12`
- maximum consecutive empty weeks: `3`

Every implementation, metric, exact 3% NAV loss budget, global one-slot, partial-fill protection, liquidation and engine audit passed. The failure is logical and structural.

## Controlled result

V7 changed only AAC entry ownership. The detector already observed outside acceptance, one defended pullback and a later reacceleration. Instead of resting again at the old pullback pivot, V7 entered at the completed reacceleration close when unchanged costed structural geometry qualified.

Only two AAC plans qualified:

| Time | Symbol / direction | Net structural R | Result |
|---|---|---:|---:|
| 2026-06-17 08:44 UTC | XRP short | 1.4167 | loss |
| 2026-07-09 07:00 UTC | BTC long | 1.7511 | loss |

Both stopped. Four other AAC instructions became no-trades because confirmation-time geometry was insufficient. Aggregate NAV improved versus v6 because the candidate avoided several negative passive fills, not because the new AAC entry population had positive expectancy.

## Interpretation

The inherited second-pullback limit did contain adverse selection, but removing it did not complete the market state. A completed outside close, defended pullback and reacceleration still mixed durable inventory transfer with temporary outside acceptance inside a larger unresolved auction. Therefore V7 is not repaired by another entry-price or magnitude threshold.

The next candidate must represent acceptance failure explicitly:

```text
accepted-auction origin
→ deep boundary re-entry records failure
→ no same-bar reversal
→ later opposite initiative owns a new leg
→ failure-bar invalidation and still-live opposing external draw
```

The inspected L1 interval remains development data and cannot be reused as a holdout or success claim.
