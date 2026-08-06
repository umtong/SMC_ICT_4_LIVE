# Candidate 01 v35 — Position-Build Then Release Confirmed Failed-Sweep MSS

## Frozen question

Does the frozen v23 failed-sweep reversal become materially more reliable when
official open interest first expands into the swept-direction initiative and
then contracts by the completed market-structure shift?

V35 froze all v23 scenario and execution logic:

- causal 40-bp intrinsic liquidity sweep;
- close back inside the swept pivot and initiative/reversal aggressive-flow
  sign change;
- completed aligned-flow MSS through the nearer opposing pivot;
- full failed-sweep-to-MSS adverse path plus one 7-bp side-cost buffer as the
  structural stop;
- nearest causally active, unconsumed completed-day/week external-liquidity
  target beyond the farther local pivot;
- first official venue TradeTick strictly after the completed MSS;
- NautilusTrader 1.230.0 execution, 7 bps per side and current-NAV 3% planned
  loss.

The sole primary variable was official Binance Vision BTCUSDT
`sum_open_interest`:

1. at the actual confirmed directional-change sweep pivot, the latest causally
   available five-minute OI observation had to be strictly above its immediate
   predecessor;
2. by the completed MSS, a strictly later causally available OI observation had
   to be strictly below the sweep observation.

Every metrics row was conservatively unavailable until five minutes after its
archive timestamp. No OI magnitude, percentile, ratio, session, volatility or
PnL-fitted threshold was used. The single ablation removed only this OI cycle
confirmation.

## Implementation errors separated from logic

Two implementation errors were found before accepting performance evidence.
Neither changed the strategy variable, week, orders, stop, target, cost or risk.

1. **Incorrect causal alignment.** The first implementation measured OI build
   at failed-sweep confirmation rather than the actual sweep pivot. It was
   corrected to use the detector's frozen `pivot_time_ns`, and the same week was
   rerun.
2. **Unused nullable archive columns.** Some official 2022 metrics rows contain
   blanks in long/short-ratio columns that v35 never reads. The loader initially
   rejected those rows while the required OI and OI-value fields were present.
   Only unused auxiliary columns were made nullable; OI fields, timestamps,
   symbol, ZIP integrity and SHA-256 checks remained strict. The same week was
   rerun again.

Only the final causally aligned and successfully validated rerun below is used
for the logical decision.

## Authoritative first frozen BTC week

- Evaluation: `2022-11-28T00:00:00Z` to `2022-12-05T00:00:00Z`
- Engine: NautilusTrader `1.230.0`
- Execution data: official Binance Vision USD-M aggregate trades converted
  one-for-one to `TradeTick`
- Positioning data: official Binance Vision USD-M BTCUSDT metrics, verified by
  archive checksum
- Custom fill, PnL or NAV simulator: none
- Global pending-entry plus open-position limit: one

### Primary — OI build then release

| Diagnostic | Result |
|---|---:|
| frozen v23 evaluation plans | 15 |
| OI-cycle-confirmed plans | 5 |
| OI did not contract by MSS | 6 |
| OI did not expand into sweep | 4 |
| Nautilus submissions / closed positions | 3 / 3 |
| wins | 0 |
| total return | **-4.6596%** |
| geometric daily return | **-0.6793%** |
| profit factor | **0.0000** |
| maximum drawdown | **-4.6596%** |
| insufficient-net-RR rejections | 2 |

The three fills were one structural stop and two negative four-hour exits. No
retained plan reached the selected calendar destination.

### Single ablation — frozen v23 MSS control

| Diagnostic | Result |
|---|---:|
| selected plans | 15 |
| Nautilus submissions / closed positions | 9 / 9 |
| wins | 1 |
| win rate | **11.11%** |
| total return | **-14.2885%** |
| geometric daily return | **-2.1785%** |
| profit factor | **0.0919** |
| maximum drawdown | **-14.2885%** |
| insufficient-net-RR rejections | 6 |

The OI confirmation removed six executed control trades and reduced the loss,
but all three trades it retained still lost. The only profitable control trade
was rejected by the OI cycle because OI did not contract by its MSS.

## Interpretation

The result distinguishes a useful state observation from an actual trading
edge:

- OI build-and-release was causally observable and materially selective;
- it reduced exposure to an already poor v23 population;
- it did not identify a profitable subset;
- the retained trades failed before destination, so target distance was not the
  dominant issue;
- the failure cannot be repaired by tuning an OI magnitude threshold without
  turning the research into outcome fitting.

Open-interest contraction is also directionally ambiguous by itself: it
indicates contracts were reduced or settled, but not whether the dominant
release was trapped longs, trapped shorts or voluntary profit-taking. V35 used
price/aggressive flow for direction, yet the frozen v23 failed-sweep state was
still not reliable enough for OI release to rescue.

## Surviving components

- official five-minute positioning archives can be loaded causally with strict
  checksums and stable OI fields;
- OI build/release is a meaningful diagnostic dimension and should remain
  available for scenario attribution;
- it should not be used as a generic filter on a weak direction state;
- the next candidate needs a source scenario with independently demonstrated
  directional edge before adding positioning confirmation.

## Decision

`STOP` — discard v35. Do not open its second or third frozen weeks. Preserve the
verified positioning loader and diagnostics, but do not continue threshold
search on this v23 family.
