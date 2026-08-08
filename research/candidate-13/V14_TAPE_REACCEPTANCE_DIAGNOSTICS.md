# Candidate 13 V14 — causal tape/re-acceptance diagnostic

This is an **exposed-development diagnostic**, not independent validation.
All one-minute bars are checksum verified and become visible at `open_time + 1 minute`.

## Accounting

- submitted plans: 24
- matched outcomes: 24
- causal bar records: 24

## Tape states

| State | Trades | Wins | Losses | Net PnL |
|---|---:|---:|---:|---:|
| FAILED_ABSORPTION_OR_FLOW_REVERSION | 1 | 1 | 0 | 3829.36 |
| NON_FAR_UNCHANGED | 3 | 3 | 0 | 25837.52 |
| REVERSAL_RECLAIM_WITH_AGGREGATE_FLOW | 3 | 3 | 0 | 13105.61 |
| REVERSAL_RECLAIM_WITH_PERSISTENT_FLOW | 13 | 10 | 3 | 50435.55 |
| UNRESOLVED_SINGLE_BAR_CONFIRMATION | 4 | 4 | 0 | 28077.49 |

## Interpretation contract

- Two consecutive closes beyond the swept extreme after reclaim means true re-acceptance; FAR reversal is not authorized.
- Two consecutive closing bars with direction-aligned aggressive flow authorize persistent reversal confirmation.
- A single displacement bar without persistent or aggregate flow remains unresolved.
- These rules are structural sign/streak rules; no PnL-fitted threshold is selected here.
