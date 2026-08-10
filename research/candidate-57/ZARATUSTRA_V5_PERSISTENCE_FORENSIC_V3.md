# Candidate 57 — ZaratustraV5 persistent-thesis forensic v3

## What v2 falsified

The behaviour-identical lifecycle audit showed that a *first* loss of the full
5m/15m/30m source condition is not a valid exit by itself.  Almost every winner
reached the public trailing activation, but 47.2% of winners also lost the full
source condition at least once.  A one-check source invalidation would therefore
cut a large part of the winner engine even though it identifies 84.1% of partial
losses and 88.5% of full stops.

The next uncertainty is not a numeric stop distance.  It is whether winners
usually experience a short lower-timeframe flicker while losing trades undergo
a persistent, multi-timeframe collapse of the original thesis.

## Predeclared causal hypothesis

> Before trailing activation, winners may temporarily fail one 5-minute source
> component but recover while the 15-minute and 30-minute context remains
> intact.  Full-stop and material max-hold losses persist outside the same-side
> source state for at least one complete 15-minute information interval, often
> losing two or more timeframe contexts before reaching -0.50R.

This is a behaviour-only diagnostic.  Entry, arbitration, source stop, target,
trailing, max hold, risk sizing and fills remain unchanged.

## Predicted observations

If the hypothesis is correct:

1. first source invalidation will overlap winners and losers, as v2 already
   showed;
2. a three-check invalidation streak (three completed 5-minute observations,
   one complete 15-minute interval) should occur before trailing activation in
   few winners;
3. the same streak should occur before -0.50R in most full-stop losses;
4. full-stop losses should show failures in at least two timeframe contexts,
   not merely a transient 5-minute RSI or Bollinger failure;
5. the mark at the first three-check persistent failure should remain
   materially better than the eventual near-full stop;
6. the instrumented account must exactly reproduce the frozen 214-trade June
   account.

## Falsification

The persistence hypothesis is rejected if many winners experience the same
three-check or multi-timeframe collapse before activation, if full stops reach
-0.50R before persistence becomes observable, or if the loss and winner path
statistics overlap materially.  In that case no persistence threshold is
implemented and the next investigation must move upstream to entry-state or
cross-asset selection.

## Recorded state

At every completed five-minute observation the wrapper records, for the entry
side:

- RSI, directional-indicator and Bollinger-middle truth values on 5m, 15m and
  30m separately;
- number of failed components and failed timeframe contexts;
- current and maximum consecutive source-invalidation streak;
- first two-, three- and six-check persistent invalidations and their account-R
  marks;
- first failure of 15m, 30m, two-timeframe and all-timeframe context;
- first recovery after invalidation and duration of the invalidation episode;
- whether persistence occurred before trailing activation or before -0.50R.

The two-, three- and six-check observations are not a parameter tournament.
They expose the raw duration scale of thesis failure; the only predeclared
policy-relevant scale is three checks because it spans one complete 15-minute
information interval.
