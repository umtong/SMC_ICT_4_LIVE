# Winner15m fresh one-slot arbitration decision

This is a causal mechanism decision, not a binary promotion gate.  The
2024-09-06 through 2024-09-17 data interval and 2024-09-09 through 2024-09-15
entry window are development data after this experiment.

## Implementation audit

The first run did not test the frozen `least_volume_excess` policy.  The reused
strategy ignored the selected object returned by `route_universe` and sorted
all returned decisions again by their unchanged source score.  Both branches
therefore executed the old maximum-climax policy.  The retry repaired the
actual score path and proved that the two policies selected different symbols
and produced different account trajectories.

This is an important reusable lesson: validating the router's returned winner
was insufficient because the downstream strategy performed a second
arbitration.  Future router experiments must trace the effective score into the
last decision site that submits the order.

Both repaired runs ended flat, had no active orders, maintained one global
position/entry intent and used the same signal, management, risk and cost path.

## Account results

| policy | completed trades | W/L | PF | mean after-cost R | total return | MDD | trailing exits |
|---|---:|---:|---:|---:|---:|---:|---:|
| current maximum-climax | 30 | 13/17 | 0.610 | -0.1057R | -9.521% | 12.99% | 6 |
| least volume excess | 28 | 11/17 | 0.538 | -0.1224R | -10.162% | 14.35% | 3 |

The frozen least-volume policy did not generalize from the previous development
forensic.  It reduced source trailing completions from six to three and made
the account path worse.  The old maximum-climax policy was also negative, so
this is not evidence that the old score is good.  It is evidence that volume
excess alone is not a sufficient proxy for remaining auction space.

## Direct collision choices versus continuous account effects

Seven shared collision boundaries selected different symbols.  At the
individual selected-trade level, least-volume produced a better R result at
five of the seven boundaries.  The sum of those seven selected-trade outcomes
was nevertheless lower: approximately +0.842R versus +0.988R for the old
policy.

More importantly, a global one-slot system cannot evaluate an arbitration as a
pointwise trade classifier.  The selected symbol determines how long the slot
is occupied and therefore which later independent episodes are reachable.

Examples:

- 2024-09-09 16:44 UTC: maximum-climax selected SOL and earned about +0.549R
  in 30 minutes, then the account could take an XRP episode at 17:14 for about
  +0.146R.  Least-volume selected ETH for about +0.648R but occupied the slot
  for 274 minutes.  The apparently better immediate trade produced less total
  account opportunity than the SOL-plus-XRP sequence.
- 2024-09-11 01:44 UTC: maximum-climax selected BTC for about +0.507R in 264
  minutes.  Least-volume selected XRP for approximately -0.003R and occupied
  the slot for the full 361 minutes.
- 2024-09-13 15:29 UTC: maximum-climax selected SOL for about +0.395R in 202
  minutes.  Least-volume selected XRP for only +0.018R and held it for 361
  minutes.
- 2024-09-15 21:14 UTC: least-volume selected SOL for about +0.499R versus
  ETH's +0.413R, but SOL consumed 265 minutes versus ETH's 119 minutes.  The
  old path subsequently reached another ETH episode at 23:14 that earned about
  +0.516R.

Across the two trajectories, 14 identical completed trades contributed about
-3.39R in either policy.  The trades unique to the old path contributed about
+0.222R; the trades unique to the least-volume path contributed about -0.035R.
The common negative core shows that arbitration alone cannot repair the base
state selector in this interval.

## Profit and loss engines

The old policy's six source-trailing exits were all winners and contributed
about +4.21R.  Its 18 time exits contributed about -3.29R, four hard-stop-like
closures contributed about -3.93R, and two fill-risk invalidations contributed
about -0.15R.

The least-volume policy retained only three trailing winners, contributing
about +2.98R.  Its 20 time exits contributed about -3.30R, three hard-stop-like
closures about -2.96R and two fill-risk invalidations about -0.15R.

Thus the useful engine is not `low volume`; it is a candidate that will develop
far enough to activate and complete the trailing trend leg before consuming the
six-hour account slot.  The main failure engine is a source-qualified trend
state that never develops and then exits by time or hard stop.

## What is disproved and what is preserved

Disproved:

- selecting the smallest volume excess after the source threshold is met;
- treating arbitration as a pointwise best-trade problem without slot duration;
- assuming the earlier post-outcome collision diagnostic would transfer without
  a fresh causal test;
- expecting arbitration alone to rescue a negative common opportunity core.

Preserved:

- the public signal repeatedly creates source-trailing winners;
- cross-symbol arbitration materially changes the continuous one-slot account;
- the candidate set at every collision must be retained in evidence;
- account-slot opportunity cost belongs inside the routing decision;
- state selection and arbitration should be tested separately before combining.

## Next system implication

The one-slot objective is closer to expected **R per occupied account time**
under the current opportunity set than to expected trade R alone.  That does
not justify fitting an `R/hour` predictor from seven observed outcomes.  It
does define the missing causal variables to obtain before another arbitration
rule is frozen:

- probability and expected time to trailing activation;
- momentum/participation decay after the signal, not just signal magnitude;
- relative extension and remaining same-leg objective space;
- broad-market leader/follower state;
- likelihood that an occupied position is still progressing versus blocking a
  stronger independent episode.

The next Winner research should therefore use an externally reused regime and
progress-state component to separate trailing-capable trends from six-hour
no-progress states.  Only after that selector has independent evidence should a
new one-slot arbitration combine candidate quality with expected slot duration.
Long validation is not justified by either arbitration policy.
