# Candidate 57 — ZaratustraV5 all-candidate and arbitration forensic v4

## Why this experiment follows v2 and v3

The frozen June account contains a genuine high-density winner engine but loses
because its loss tail is too large.  A first source-state invalidation and a
persistent three-check invalidation were both falsified as clean lifecycle
repairs: too many winners experience the same apparent failures before their
trailing activation.  The next uncertainty is therefore upstream of management.

The public router ranks simultaneous candidates by a score that increases with:

- distance of RSI from 50;
- directional-indicator excess over 25;
- distance from the 5m, 15m and 30m Bollinger middles.

In other words, the one-slot account selects the **most extended** member of a
same-timeframe trend cluster.  This may be a valid leadership measure, or it may
systematically buy or sell the member whose remaining objective space is most
exhausted.  Aggregate account results cannot distinguish those explanations.

## Predeclared causal hypothesis

> When several universe members enter the same Zaratustra state at the same
> completed five-minute boundary, the maximum source score overweights
> extension rather than continuation quality.  A less extended candidate in the
> same causal market episode should retain the shared higher-timeframe context
> while offering more remaining auction space and less immediate adverse
> excursion.

This diagnostic does not invert the score or alter an order.  It records all
continuous source episodes and replays each as a non-trading shadow scenario.

## Episode definition

A new independent candidate is created only when a symbol changes from no
source state (or the opposite side) into a same-side source state.  Consecutive
five-minute level signals inside the same state are one causal episode, not new
trades.  Raw level signals are counted separately so opportunity density is not
inflated.

A collision boundary contains at least two new continuous episodes at the same
completed five-minute timestamp.  The existing maximum-score choice, the
minimum-score candidate and every rejected candidate are all retained.

## Frozen shadow lifecycle

Each candidate uses the unchanged public source geometry:

- source 2.96% price stop;
- +0.71% trailing activation;
- 0.13% trailing distance;
- 480-minute source horizon;
- conservative minute ordering and the project's declared 21 bp round-trip
  cost reserve;
- no account orders and no capital reuse.

The shadow result is diagnostic opportunity geometry.  NautilusTrader remains
the authority for the actual account.

## Predicted observations

If the hypothesis is correct:

1. on collision boundaries the maximum-score candidate should frequently not
   be the best ex-post path;
2. the minimum-score candidate should have lower early MAE and higher remaining
   MFE on a majority of collision boundaries;
3. the maximum-score minus minimum-score return difference should be negative
   across many boundaries, not only one outlier;
4. losses shared by every candidate at a boundary should remain identifiable as
   common-mode state failures and must not be attributed to arbitration;
5. raw signal count should greatly exceed continuous causal episodes, showing
   why level re-entry count cannot be used as independent opportunity density;
6. the instrumented account must exactly reproduce the frozen 214-trade June
   account.

## Falsification

The arbitration hypothesis is rejected if collision count is too small, the
maximum-score candidate is not systematically worse, score rank has no stable
relationship to MFE/MAE or result, or most bad boundaries are common-mode losses
across all candidates.  In that case no score inversion is implemented and the
next investigation must focus on the shared market-state selector.

## Decision after the audit

Only a broad, repeated rank effect justifies one minimal arbitration experiment
on an untouched interval.  The entry state, source stop, target, trailing and
one-slot constraint will remain frozen.  If the audit instead identifies
common-mode failure, external research will target a market-wide trend
continuation/exhaustion state variable rather than another indicator threshold.
