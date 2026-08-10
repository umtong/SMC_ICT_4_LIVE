# Candidate 57 — ZaratustraV5 lifecycle forensic v2

## Why this experiment exists

The frozen public ZaratustraV5 account is not rejected merely because its
30-day net result is negative.  It completed 214 one-slot trades in 30 days and
won 144 of them, so it already contains a high-density directional/trailing
engine.  The account still lost because gross losses outweighed many small
trailing wins.  Aggregate PF does not tell us whether this is an unfixable entry
problem or a separable trade-lifecycle problem.

The source account is therefore replayed without any policy change.  Only
causal path information is recorded.

## Current causal model

A ZaratustraV5 entry requires 5m, 15m and 30m agreement in RSI, directional
movement and position versus the Bollinger middle.  The source then waits for a
0.71% favourable move, after which it trails by 0.13%.  Before activation it
risks the source-normalized 2.96% price stop and the project currently returns
the slot after 480 minutes.

The observed account had:

- frequent trailing winners;
- a material set of near-full source-stop losses;
- a second set of partial losing 480-minute exits;
- enough gross winner production that a separable lifecycle failure would be
  more valuable than another unrelated strategy search.

## Predeclared hypothesis

The high-information hypothesis is not “use a tighter stop” or “reduce the
hold time.”  It is:

> losing trades cease to satisfy the original multi-timeframe trend thesis
> before they reach the trailing activation, while winners generally reach the
> activation before the original thesis is invalidated.

If this temporal ordering is present, the next minimal policy experiment will
keep entry, arbitration, risk sizing, source stop, target and trailing
unchanged.  It will add only a causal thesis-failure exit after the same-side
source state is lost before trailing activation.

## Predicted observations

If the hypothesis is correct:

1. most winners should show `activation_before_invalidation` or no observed
   invalidation before their trailing exit;
2. most full-stop losses should show
   `invalidation_before_activation` or invalidation without activation;
3. the mark at first invalidation should usually be materially better than the
   eventual near-full stop;
4. partial max-hold losses should spend a low fraction of their lifecycle in
   the original same-side state;
5. the behaviour-instrumented account must reproduce the frozen account's
   trades, PnL, PF, growth and drawdown exactly.

## Falsification

The hypothesis is rejected when winner and loser temporal ordering overlaps
materially, when invalidation usually occurs only after losses are already
large, or when many winners lose the source state before activation and later
recover.  In that case a source-state exit would only raise win rate by cutting
both good and bad trades and should not be implemented.

## Recorded path

For every completed trade the wrapper records:

- minute-by-minute MFE, MAE and close in source-risk units;
- first time to +0.10R, +0.24R, +0.50R and +1.00R;
- first time to -0.10R, -0.25R, -0.50R and -0.75R;
- mark, MFE, MAE and trailing state at 5, 15, 30, 60, 120, 240, 360 and
  480 minutes;
- first completed 5-minute loss of the original same-side source state;
- first opposite source state;
- fraction of completed source-state checks still agreeing with the entry;
- explicit source trailing, max-hold, evaluation-end or source-stop exit family.

This is diagnostic use of already consumed June 2026 data.  It does not create
new holdout evidence and cannot promote the strategy by itself.
