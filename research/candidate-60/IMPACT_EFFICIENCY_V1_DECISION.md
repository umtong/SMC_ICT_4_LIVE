# Candidate 60 — impact-efficiency V1 forensic decision

## Development result

The data, prior-only regime baseline, separate confirmation block, next-open
entry and one-slot opportunity contracts were valid. Neither family earned the
predeclared August 3–9 fresh interval.

| family | horizon | one-slot events | mean gross bp | mean net bp | positive symbols | positive days |
|---|---:|---:|---:|---:|---:|---:|
| impact persistence | 15m | 226 | +0.5643 | -19.4357 | 0/4 | 0/7 |
| absorption release | 15m | 340 | +0.6741 | -19.3259 | 0/4 | 0/7 |

The continuation direction outperformed its opposite, and the delayed
absorption-release direction also outperformed its opposite. That establishes a
small statistical orientation, not a tradeable distribution. Every asset and
every scored day remained negative after the 20 bp project friction floor.

At longer horizons, absorption release reached +3.5444 bp gross at 30 minutes
and +3.1174 bp at 60 minutes. SOL was the strongest 30-minute asset at roughly
+10.03 bp gross, but even this remained below round-trip cost. Impact persistence
was approximately flat before costs at 5–30 minutes and negative by 60 minutes.
No adjacent horizon can rescue the frozen 15-minute failure.

## What the experiment learned

Separating state from confirmation corrected the conceptual error in the prior
failed-auction implementation. However, the available one-minute executed-flow
proxy still collapses several economically different events:

- informed metaorder continuation;
- uninformed urgency absorbed by passive inventory;
- common information already incorporated across venues;
- hedging or liquidation flow whose remaining price impact is exhausted;
- high flow in a high-liquidity state where the residual move is too small to
  monetize.

A ratio above or below the prior-day median tells us whether impact is unusual,
but not why it is unusual or whether enough unconsumed target space remains.
The observed gross orientation was one to three basis points—far below the
required economic margin.

## Decision

The exact executed-flow impact-ratio policies are closed without searching
absolute flow thresholds, impact quantiles, baseline lengths, sides, symbols,
clock subsets or alternative holding periods on the consumed July 13–19 data.
The August 3–9 interval remains untouched.

Useful components retained:

1. strict separation of parent auction, state classification, later initiative
   and entry;
2. prior-only normalization to the current liquidity regime;
3. one-slot non-overlap diagnostics before expensive Nautilus integration;
4. explicit comparison against the exact opposite direction and realistic
   cost floor.

A successor must introduce a genuinely new economic observable—such as
leader/follower information diffusion, actual liquidation identity, cross-venue
basis innovation, or source-faithful queue resiliency—and must explain why the
remaining move can exceed costs rather than merely improve directional
correlation.
