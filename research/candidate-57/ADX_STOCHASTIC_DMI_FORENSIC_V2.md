# Candidate 57 — ADXStochastic directional-state forensic v2

## Why this experiment exists

The public ADXStochastic entry combines a very high ADX with an oversold fast
stochastic bullish cross, but ADX measures trend strength rather than trend
direction.  The source long rule does not require the positive directional
index to exceed the negative directional index, nor does it require the
negative directional trend to have weakened.

The frozen project account produced two apparently contradictory observations:

- development: 12 trades, 4 winners and 8 losers, with several near-full stops;
- reserved: 5 trades and 5 winners.

Changing the ADX or stochastic thresholds would not explain this contrast.  A
behaviour-identical replay is therefore used to test whether the missing
variable is directional-state transition rather than indicator magnitude.

## Predeclared causal hypothesis

> The profitable episodes are not merely oversold crosses inside any strong
> trend.  They are crosses occurring after negative directional pressure has
> weakened or after +DI has overtaken -DI.  Full-stop losses remain in a strong
> negative directional state even though the oscillator briefly crosses up.

This is a state-transition hypothesis, not a proposed threshold search.

## Predicted observations

If the hypothesis is correct:

1. winners should either enter with `+DI > -DI` or reach that state before their
   ROI exit;
2. full-stop losses should remain `-DI > +DI`, or reach -0.50R before the first
   bullish DMI cross;
3. the development/reserved difference should be visible in DMI ordering and
   its timing, not only in ADX or stochastic magnitude;
4. the first bullish DMI cross should occur while meaningful objective space
   remains; otherwise it is too late to be an entry confirmation;
5. the instrumented accounts must reproduce both frozen accounts exactly.

## Falsification

The hypothesis is rejected if winners commonly reach the ROI target without a
bullish DMI transition, if losing trades commonly cross to `+DI > -DI` before
large adverse excursion, or if the two periods have materially overlapping DMI
paths.  In that case adding a DMI filter or confirmation would only remove
trades without identifying the causal loss state.

## Recorded path

For each trade the diagnostic wrapper records:

- +DI, -DI, DMI spread and ADX at entry;
- one- and three-candle ADX slope;
- first completed five-minute bullish DMI cross;
- first completed five-minute weakening of negative directional pressure;
- DMI values and mark-to-entry R at those events;
- MFE/MAE and time to +0.10R, +0.25R, -0.10R, -0.25R and -0.50R;
- explicit ROI, source-signal, max-hold, evaluation-end or source-stop exit
  family.

The already consumed development and reserved periods are used only for
mechanism diagnosis.  No result from this audit is holdout evidence.
