# 4h jump reversal — fresh arbitration × boundary-handoff decision

This is a causal mechanism decision, not a binary gate.  The 2026-04-01 through
2026-04-14 interval is now development data.

## Validity

All four frozen cells completed in NautilusTrader with the same source signal,
structural stop, 240-minute horizon, transient 0.4R arm / 1.0R escape policy,
realistic project costs, current-NAV 3% planned-loss sizing and one global
pending entry or position.  Every cell ended flat with no active orders.

The experiment compared:

- source highest absolute z-score versus least absolute qualifying z-score at
  simultaneous four-major boundaries;
- current control flow versus deferred flat-account handoff when a new completed
  4-hour boundary appears exactly as the old position reaches its source
  horizon.

## Account result

| arbitration | handoff | trades | W/L | PF | mean after-cost R | total return | geo/day | MDD |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| source max-z | no | 9 | 3/6 | 0.332 | -0.265R | -7.091% | -0.524% | 9.739% |
| source max-z | deferred | 9 | 3/6 | 0.332 | -0.265R | -7.091% | -0.524% | 9.739% |
| least qualifying z | no | 9 | 3/6 | 0.586 | -0.144R | -4.039% | -0.294% | 8.553% |
| least qualifying z | deferred | 9 | 3/6 | 0.586 | -0.144R | -4.039% | -0.294% | 8.553% |

Both arbitration policies were negative because the common market-event state
selector admitted too many continuation/no-reversal boundaries.  The least-z
policy nevertheless improved the continuous account by approximately 1.09R
relative to max-z on the same nine executed boundaries.  This relative effect
is useful, but it does not rescue the family in this interval.

## Arbitration episode anatomy

Three single-candidate boundaries were identical between the policies.  Six
collision boundaries selected different symbols.

- 2026-04-05 23:59 UTC: max-z BTC short -0.592R; least-z SOL short -0.943R.
  Least-z was materially worse.
- 2026-04-06 23:59 UTC: max-z SOL long -0.967R; least-z BTC long -0.248R.
  Least-z reduced the loss by about 0.72R.
- 2026-04-07 23:59 UTC: max-z ETH short +0.043R; least-z SOL short +0.428R.
- 2026-04-11 19:59 UTC: max-z ETH short +1.208R; least-z XRP short +1.572R.
- 2026-04-12 03:59 UTC: max-z SOL long -0.257R; least-z XRP long -0.285R.
- 2026-04-13 23:59 UTC: both choices were approximately flat, +0.015R versus
  +0.017R.

Least-z improved four of six collision choices, worsened two, and preserved the
large 2026-04-11 winner.  This is stronger evidence than the earlier
post-outcome development diagnostic because the policy was frozen before this
interval.  It is still only a small short sample, and its account remained
negative.  The component should be retained as the current better arbitration
candidate, not promoted as a complete solution.

The result also shows why arbitration cannot be judged by win rate alone.  Both
policies had the same 3/6 W/L count.  The difference came from which large loss
was avoided and which large winner was selected.

## Boundary handoff was under-informative, not disproved

The deferred handoff branch froze and submitted zero new decisions in both
arbitration policies.  At four source-horizon checks the strategy found no new
qualifying completed boundary.  Therefore the handoff and no-handoff account
paths were exactly identical.

This interval did not test the economic value of handoff.  It only verified
that enabling the control-flow repair does not alter the account when no exact
handoff opportunity exists.  The policy remains a valid rare-event component
because the earlier 2025-12 audit exposed a real missed independent boundary;
it needs either a targeted diagnostic interval containing such an event or a
larger but still diagnostic scan.  It must not be declared successful or failed
from this zero-opportunity sample.

## Profit and loss structure

The source max-z account produced one transient-protection winner and four
source-horizon exits; the remaining trades closed through structural/fill-risk
paths represented in the report-only class.  The least-z path produced two
transient-protection exits and four source-horizon exits.  The strongest winner
in either account was the 2026-04-11 short reversal; the dominant losses came
from boundaries that continued in the impulse direction rather than reversing.

Thus the primary missing component is still **market-event state**, not a finer
z-score selector or another stop-management threshold.  Arbitration can improve
which symbol expresses an already-valid reversal state, but it cannot convert
an all-market continuation event into a reversal.

## Preserved and rejected elements

Preserve:

- whole-impulse structural invalidation;
- transient 0.4R / 1.0R management;
- least-qualifying-z as the better currently frozen cross-symbol candidate;
- deferred handoff code as a verified no-overlap control-flow component;
- causal boundary grouping: simultaneous symbol signals are one market-event
  family, not independent trades.

Reject or postpone:

- source max-z as the default one-slot arbitration when a better frozen contrast
  is available;
- any claim that arbitration alone solves the negative common selector;
- any handoff performance conclusion from an interval with zero handoff
  opportunities;
- long validation of this version.

## Next action

The next information-rich action is the separately frozen Binance peer-taker
state experiment on the same interval.  It asks whether observable aggressive
flow after the completed impulse rejects the continuation boundaries while
preserving the high-payoff reversal episodes.  That state component was frozen
before this arbitration result was read, so its comparison remains causally
interpretable.  Only after the state selector has evidence should it be combined
with the least-z arbitration on a different untouched interval.
