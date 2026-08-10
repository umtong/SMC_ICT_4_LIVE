# 4h jump reversal — fresh peer-taker state decision

This is a causal market-state decision, not a binary gate.  The 2026-04-01
through 2026-04-14 interval is now development data.

## Validity

Both cells used the same completed 4-hour jump signal, whole-impulse structural
stop, 240-minute source horizon, transient 0.4R arm / 1.0R escape policy,
source maximum-z cross-symbol arbitration, current-NAV 3% planned-loss sizing,
realistic project costs and one global pending entry or position.

The external state used Binance Vision USD-M futures metrics with a strict
as-of join.  All four peer snapshots had to exist and be no older than ten
minutes.  No post-boundary metric was used as an entry-time feature.  Both
accounts ended flat with no active orders.

## Account result

| policy | independent boundaries traded | W/L | PF | total return | geo/day | MDD |
|---|---:|---:|---:|---:|---:|---:|
| source without taker filter | 9 | 3/6 | 0.332 | -7.091% | -0.524% | 9.739% |
| peer taker alignment 3-of-4 | 5 | 2/3 | 0.759 | -1.135% | -0.0815% | 7.072% |

The frozen external-state component removed approximately 2.05R of actual
account loss and reduced the two-week drawdown materially.  It preserved the
largest +1.21R winner and the smaller positive reversal boundary.  The filtered
account remained slightly negative because two aligned boundaries still failed.

This is a useful reusable component, not a complete strategy.

## What was rejected

The filter rejected four completed 4-hour market-event boundaries:

- 2026-04-07 19:59 UTC: short reversal, 0/4 peers aligned; actual source trade
  approximately -0.96R and the audited source candidate hit structural stop.
- 2026-04-12 03:59 UTC: long reversal, 2/4 peers aligned; actual source trade
  approximately -0.26R and all four peer candidates were negative at the source
  horizon.
- 2026-04-13 19:59 UTC: short reversal, 0/4 peers aligned; actual source trade
  approximately -0.85R and the audited source candidate hit structural stop.
- 2026-04-13 23:59 UTC: short reversal, 2/4 peers aligned; source account result
  was only about +0.015R and the peer paths were near flat.

The rejected group contained ten negative and only one positive symbol-level
shadow path.  Its diagnostic shadow sum was approximately -2.82R.  The filter
therefore removed genuine continuation/no-reversal states rather than merely
raising win rate by deleting large winners.

## What was preserved

The filter retained five boundaries:

- 2026-04-05 07:59 UTC: four peers aligned with a long reversal; near break-even.
- 2026-04-05 23:59 UTC: four peers aligned with a short reversal; all four
  candidates nevertheless hit structural stop.
- 2026-04-06 23:59 UTC: three peers aligned with a long reversal; all four
  candidates were negative, although some peers lost far less than the selected
  SOL trade.
- 2026-04-07 23:59 UTC: four peers aligned with a short reversal; positive peer
  reversal paths.
- 2026-04-11 19:59 UTC: four peers aligned with a short reversal; the strongest
  winning boundary, approximately +1.21R in the source-max account.

The accepted group remained negative in aggregate because taker direction alone
does not distinguish exhaustion from aggressive-flow continuation in every
case.  It correctly identifies a necessary reversal-state feature, but not a
sufficient one.

## Causal interpretation

The external component solves a different problem from the price jump and the
cross-symbol arbitration:

- the 4-hour price impulse defines the abnormal market event;
- the peer taker state asks whether aggressive futures flow has already flipped
  toward the proposed reversal;
- arbitration chooses which already-valid symbol should occupy the one global
  account slot;
- stop and management govern the selected auction leg.

This separation is valuable.  The same observation is not being reused to
create the event, confirm itself and choose the symbol.

The 3-of-4 filter generalized in the intended direction from the 2025-12
development audit: it rejected broad continuation losses while retaining the
large reversal episode.  It did not overfit to a single instrument name or one
exact taker-ratio magnitude.

## False positives that remain

The two important accepted failures were different:

1. 2026-04-05 23:59 UTC: all four taker ratios were below one, yet every short
   reversal candidate hit structural stop.  Directional taker alignment was
   present, but the broader leverage/positioning transition did not indicate
   durable exhaustion.
2. 2026-04-06 23:59 UTC: three peers supported a long reversal, but the source
   max-z arbitration selected SOL for approximately -0.97R while the least-z
   BTC alternative in the separately frozen arbitration experiment lost only
   about -0.25R.  This boundary contains both a weak market-state problem and a
   symbol-selection problem.

A follow-up Binance metrics forensic is preserving the 15- and 60-minute
open-interest, positioning and taker changes around every fresh boundary.  That
is development diagnosis only; no new threshold selected from those outcomes
may be claimed as validation.

## Interaction with the independently frozen arbitration

On this same development interval, the peer-taker component and the
least-qualifying-z arbitration address complementary failures:

- taker alignment removed four bad/no-value market-event boundaries;
- least-z improved four of six collision choices but could not remove an
  all-market continuation boundary;
- applying both decisions counterfactually to the already observed account path
  would preserve the filter's boundary rejection and choose less damaging or
  more profitable peers at several accepted collisions.

That interaction must not be declared successful from a post-outcome
counterfactual.  The correct next step is a pre-frozen combined policy on a
different interval.

## Preserved and rejected elements

Preserve:

- peer taker alignment as a higher-level reversal-state component;
- strict as-of freshness and all-four-peer availability;
- whole-impulse structural stop and transient management;
- independent 4-hour boundary counting;
- least-z as the currently better separately frozen arbitration candidate.

Reject or postpone:

- taker alignment as a sufficient standalone state classifier;
- any symbol-specific exception learned from this interval;
- tuning the 3-of-4 majority or ratio threshold from these outcomes;
- long validation before the combined state/arbitration policy is tested on a
  different untouched interval.

## Next action

Freeze and test the already established combination—peer taker alignment plus
least-qualifying-z arbitration—on a different short interval, with the source
max/no-filter and source max/taker paths retained as causal controls.  If the
combined system does not preserve large winners and materially improve the
continuous account, return to leverage/positioning state rather than adjusting
price thresholds or management.
