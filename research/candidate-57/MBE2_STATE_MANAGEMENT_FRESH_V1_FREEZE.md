# Candidate 57 — frozen MBE2 state-dependent management fresh experiment

## Evidence that motivates this test

The public MBE2 short entry has produced two materially different causal
states in prior development intervals:

- a **single-symbol source episode**, where only one of BTCUSDT, ETHUSDT,
  SOLUSDT and XRPUSDT is actionable at a completed five-minute boundary;
- a **cross-asset breadth episode**, where two or more symbols independently
  satisfy the same completed-candle source condition at that boundary.

Across the prior May and June decompositions, breadth episodes had much higher
cost-after quality but lower frequency.  A universal 240-minute horizon improved
slot turnover for the mixed/single-heavy account, while cutting the long path of
some breadth winners.  The next useful question is therefore not another entry
threshold.  It is whether management should depend on the already-observed
market-event state.

## Fresh interval

- scored entry interval: **2026-07-01 through 2026-07-14 UTC**;
- warm-up and run-off follow the reusable MBE2 campaign contract;
- this interval was not used to choose the three policies below;
- the interval becomes development data as soon as the first result is read.

## Three frozen cells

| cell | single-symbol episode | breadth >= 2 episode |
|---|---:|---:|
| `roi_open_control` | 10,080-minute safety horizon | 10,080-minute safety horizon |
| `roi_h240_control` | 240 minutes | 240 minutes |
| `roi_state_hybrid` | 240 minutes | 10,080-minute safety horizon |

The 10,080-minute value is only a non-binding seven-day safety ceiling.  The
public ROI ladder and end-of-evaluation flatten remain active.

## State definition

The source adapter evaluates all four symbols on the same completed five-minute
boundary before one-slot arbitration.  It stores the number of additional
unused actionable candidates in `mbe_collision_competitors` when a trade is
submitted.

- `mbe_collision_competitors == 0`: single-symbol state;
- `mbe_collision_competitors >= 1`: breadth state.

This field is known before entry.  No future price path, eventual exit reason,
asset name or outcome label enters the state definition.

## Frozen source and execution contract

Unchanged in every cell:

- public completed-five-minute MBE2 short entry;
- RSI 70 down-cross;
- TEMA above the Bollinger middle and falling;
- reported 6.46x profit-ratio semantics;
- public ROI schedule, including the source 114-minute value;
- ROI-only management: no trailing branch;
- no cross-asset breadth rejection at entry; all source episodes remain eligible;
- existing source arbitration when multiple symbols are actionable;
- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
- one global pending entry or open position;
- current-NAV 3% planned-loss sizing;
- realistic fees, adverse slippage, funding safety and NautilusTrader matching;
- continuous NAV and end-flat account validity.

## Interpretation

The hybrid is useful only if it preserves breadth payoff while recovering the
turnover and independent opportunity count of the 240-minute policy.  It should
be compared against both controls on the same continuous one-slot account, not
by adding separate single and breadth results.

If the hybrid is weaker than both controls, do not tune the 240-minute number on
this interval.  The state-management hypothesis is then rejected or must be
replaced by a different observable management state such as progress toward the
first ROI objective, auction participation decay or an external flow transition.
