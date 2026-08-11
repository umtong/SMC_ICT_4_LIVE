# Forced-unwind pre-impulse context v62

- source periods: 10
- enriched records: 84
- v61 reproduction: `True`
- conclusion: **multi_scale_alignment_supported**

| policy | trades | trades/day | mean R | median R | PF | ex-best R | daily diagnostic geom | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all_contexts_lifecycle_v61 | 40 | 0.286 | 0.432 | 0.174 | 3.91 | 0.394 | 0.359% | -6.22% |
| aligned_24h | 31 | 0.221 | 0.487 | 0.308 | 4.18 | 0.439 | 0.314% | -5.39% |
| aligned_24h_and_72h | 22 | 0.157 | 0.640 | 0.408 | 5.56 | 0.579 | 0.293% | -4.61% |

## Predeclared assessment

- v61_reproduced_exactly: `True`
- ex_best_expectancy_remains_positive: `True`
- bull_pullback_loss_state_improves: `True`
- latest_post_publication_state_improves: `True`
- primary_retains_nonzero_independent_opportunities: `True`

## Truth boundary

Any supported context is a router component for one sparse specialist, not a final system. It still requires untouched confirmation and continuous NautilusTrader account validation.
