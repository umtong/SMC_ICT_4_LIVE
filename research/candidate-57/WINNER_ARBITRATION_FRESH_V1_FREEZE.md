# Winner15m one-slot arbitration — frozen fresh short experiment

This experiment is frozen before reading the interval results.  It is not a
binary promotion gate and it is not a parameter search.  Its purpose is to
identify whether the one-slot adapter's arbitration, rather than the public
trend signal itself, is destroying useful causal episodes.

## Fresh interval

- data/warm-up: 2024-09-06 through 2024-09-08 UTC
- entry window: 2024-09-09 through 2024-09-15 UTC
- run-off and forced flatten: through 2024-09-17 UTC
- universe: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- one global pending entry or position
- current-NAV planned-loss budget: 3%
- NautilusTrader matching/accounting and project costs

The interval becomes development data after this run.  It is called *fresh* to
Candidate 57's Winner arbitration repair; it is not claimed to be globally
untouched by every prior project experiment.

## Signal and management held constant

The public Winner15m signal is held constant:

- completed 15-minute candles only
- public 200-candle startup
- EMA 10/30
- MACD 12/26/9
- ROC(3) threshold ±0.10
- ADX(14) > 18
- volume > SMA(20)
- public 2.5% stop
- public trailing activation/gap and ROI schedule

For this adaptation, a continuous true condition is collapsed to one
false-to-true causal episode so repeated source-candle truth cannot inflate the
independent opportunity count.  Maximum holding is frozen at 360 minutes to
keep this experiment in the existing day-trading adaptation.  The source-
faithful continuous-signal/long-hold anatomy is a separate experiment.

## Exactly two arbitration policies

No third policy will be selected after seeing the interval.

### A. `current_max_climax`

The prior project router: fixed source reward plus larger ADX, absolute ROC and
volume excess.  This selects the most climactic candidate when several symbols
qualify simultaneously.

### B. `least_volume_excess`

The one frozen causal repair: after every candidate already satisfies trend,
momentum, ADX and participation requirements, choose the qualifying candidate
with the smallest volume ratio above the source threshold.  The hypothesis is
that it preserves more remaining auction space instead of paying for the most
extended liquidation/participation climax.

This is not asserted to be optimal.  The development forensic selected it as a
single structural contrast to the existing rule.

## What will be inspected

The result will be decomposed, not reduced to PASS/FAIL:

- every completed trade and exit reason
- simultaneous candidate episodes and selected symbol
- positions that blocked later independent episodes
- open/inflight order state and end-flat validity
- raw wins/losses, R geometry, holding time and NAV path
- whether a policy improves results by preserving the same winner engine or by
  accidentally changing opportunity exposure
- whether failures are signal, arbitration, entry, stop, management, cost,
  implementation or normal probabilistic losses

Aggregate return is descriptive.  A policy is only reusable if the causal
episode evidence and code path explain why its behavior changed.
