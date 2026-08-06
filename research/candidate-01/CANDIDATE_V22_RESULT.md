# Candidate 01 v22 — Calendar External Target after MSS Retest

## Frozen question

Does preserving the v21 failed-sweep → MSS → broken-pivot-retest trigger while
moving the final destination from local 40-bps pivots to causally available
calendar external liquidity repair the reward side of the executable geometry?

Completed UTC-day and completed Monday-Sunday week highs/lows became active only
after the source period completed. They were removed on the first later
completed equal-notional event whose range traded the level. At failed-sweep
confirmation, the candidate selected the nearest active level in the trade
direction strictly beyond the farther local opposing pivot. No target was
selected by reward/risk or PnL.

## Authoritative first unused BTC week

- Frozen before execution: `2022-10-10T00:00:00Z` to
  `2022-10-17T00:00:00Z`
- Engine: NautilusTrader `1.230.0`
- Data: official Binance Vision USD-M aggregate trades as one-for-one TradeTicks
- Costs: 7 bps per side
- Risk: current Nautilus NAV × 3%
- Maximum hold: four hours
- Custom fill/PnL/NAV simulator: none

### Calendar-target primary

| Diagnostic | Result |
|---|---:|
| calendar levels built | 30 |
| active levels at evaluation end | 7 |
| failed sweeps armed with a causal target | 72 |
| target source | 72 day, 0 week |
| MSS confirmations | 32 |
| broken-pivot retest confirmations | 9 |
| evaluation-period plans | 5 |
| Nautilus submissions | 0 |
| closed positions | 0 |
| cost-dominated rejections | 4 |
| failed confirmation-hold rejections | 1 |
| total return | 0.00% |

The five evaluation plans had calendar destinations well beyond the local
pivots. Four examples retained very large cost-after reward/risk, but their
local post-MSS retest risk was only about 8–12 bps. With 7 bps charged on entry
and again on exit, price risk represented only 35.8%–46.4% of planned loss,
below the frozen 65% cost-resolution contract. The fifth plan lost the broken
pivot on the first executable venue trade.

### Identical-stream local-target control

| Diagnostic | Result |
|---|---:|
| MSS confirmations | 38 |
| broken-pivot retest confirmations | 7 |
| evaluation-period plans | 3 |
| Nautilus submissions | 0 |
| closed positions | 0 |
| cost-dominated rejections | 2 |
| failed confirmation-hold rejections | 1 |
| total return | 0.00% |

The calendar layer increased evaluation plans from three to five and repaired
the reward distance. It did not repair the price-risk unit because both variants
used the same narrow retest-path invalidation.

## Interpretation

The result separates two problems which earlier candidates conflated.

- Local 40-bps pivots were too shallow as the final destination after waiting
  for MSS and retest.
- Completed-day/week liquidity supplied a materially farther, causal target.
- The remaining failure came from entering after the retest while placing the
  stop just beyond that same small retest event. At the fixed execution cost,
  this creates a risk unit dominated by fees even when the directional target
  is excellent.

Adding an arbitrary minimum stop or ATR multiple would only tune around the
cost floor. The next independent candidate must preserve the calendar target
and use an earlier completed structural state whose genuine invalidation range
is naturally larger: the aligned-flow MSS displacement itself, invalidated
beyond the full failed-sweep-to-MSS path.

## Decision

`STOP` — do not open the second and third v22 weeks. Preserve the target layer;
replace only entry and invalidation stage in v23.
