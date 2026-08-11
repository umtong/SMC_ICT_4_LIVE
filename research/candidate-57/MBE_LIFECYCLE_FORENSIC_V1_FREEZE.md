# Candidate 57 — MBE lifecycle forensic v1 freeze

This is a consumed-data causal diagnostic, not a policy search. The public
MBE2 short entry, at-least-two collision requirement, source score,
BTC/ETH/SOL/XRP arbitration, stop, ROI ladder, costs, fills and current-NAV 3%
risk sizing remain unchanged.

The problem to explain is stable across prior evidence: many small ROI wins are
offset by a smaller set of negative bracket, horizon and other non-ROI exits.
The loss population is therefore **all negative completed MBE trades**, not
only the rare near-full-stop tail. Collision count alone is not a stable state
variable: exactly-two was strong in April 2026 and weak in March 2024, while
three-plus showed the opposite sign.

## Fixed observation clock

No arbitrary time grid is optimized. Each open MBE trade is observed on every
completed five-minute boundary for anatomy, and adjudicated at the source ROI
ladder's own causal ages:

- 15 minutes
- 41 minutes
- 114 minutes
- 180 minutes
- 420 minutes

At each boundary the strategy records, without changing any order:

- current source profit ratio and an estimated after-cost R;
- MFE and MAE accumulated so far;
- current raw MBE long/short cross breadth across all four symbols;
- counts of RSI re-overbought symbols;
- counts of TEMA-above-middle and TEMA-rising symbols;
- the entry symbol's RSI, TEMA gap/slope, one/four/eight-hour return,
  realized volatility and one-hour range.

The exact finite-history implementation must first reproduce the committed
April 2026 MBE-only account's episode keys, trade count, ending NAV and
expectancy. A parity failure is an implementation error and blocks all
interpretation.

## Predeclared causal predictions

If the negative non-ROI trades are failures of the mean-reversion lifecycle
rather than unavoidable noise, then before their final outcome they should show
a repeated combination of:

1. no meaningful favorable excursion after costs;
2. increasingly adverse R;
3. re-overbought or renewed TEMA-up state on the entry symbol;
4. persistent/re-ignited cross-asset short-exhaustion breadth.

ROI winners should instead show early favorable excursion and source-state
resolution. Separation must appear at source-defined ages in both consumed
months, not only in one month, one topology count or one extreme stop.

A future invalidation policy is authorized only when one simple categorical
state transition explains a majority of **all negative non-ROI trades** in
both months while preserving at least 80% of ROI winners at the same horizon.
Each month must contain at least five negative trades for the comparison. The
rare near-full-stop subset remains a reported severity diagnostic but is not
the denominator. No threshold sweep, best-price filter, symbol exception or
outcome-derived score is allowed. If the groups do not separate causally, MBE
lifecycle repair is rejected and the project moves to a different family.
