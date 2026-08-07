# Candidate-07 discarded structure clock: value-normalized one-minute MSS/FVG

## Decision

The value-normalization milestone was not the blocker. The one-minute structure
bridge is discarded because its selected swing was not local to the seconds-scale
failed-auction event. The implementation was clean; no threshold was relaxed.

## Frozen Week-1 result

The target-free volume-time detector accepted 16 events over five active days.
The baseline treated a completed return through the prior fifteen-second auction
value as normalization before beginning the MSS search:

```text
value-normalized MSS start events                  8
source invalidated before value normalization      8
one-minute MSS/FVG plans                           0
MSS/FVG not confirmed within five minutes          8
```

The controlled ablation removed only the value-normalization milestone and began
MSS search at the failed-auction recovery terminal:

```text
MSS start events                                  16
one-minute MSS/FVG plans                           0
MSS/FVG not confirmed within five minutes         16
```

Because both variants produced zero plans, the value milestone did not explain
the failure.

## Geometric diagnosis

For each ablation event, the state machine selected the latest causally confirmed
one-minute swing on the required side. Its distance from the event recovery was:

```text
median distance / causal one-minute ATR       4.0985
minimum distance / causal one-minute ATR      1.4791
maximum distance / causal one-minute ATR      8.5078

median distance / original event risk         7.8985R
maximum distance / original event risk       22.1671R
```

Representative cases:

```text
SHORT recovery 89240.3 -> swing low 89050.1
  distance 5.23 ATR / 16.51 event-R

LONG recovery 89624.8 -> swing high 90147.0
  distance 7.74 ATR / 19.95 event-R
```

Requiring this remote level to break within five minutes did not identify a
local market-structure shift. It required a large subsequent directional move
to have already occurred. Lowering displacement rank, body size or close-location
thresholds would not repair the wrong swing clock and was not attempted.

## Valid components retained

- five-minute external-liquidity source pools;
- target-free one-second volume-time failed-auction detector;
- completed value normalization as a possible state milestone;
- ranked displacement, causal FVG and first-retest sequence;
- original event extreme as structural invalidation;
- opposing pre-existing external liquidity as the objective;
- cost-positive target prerequisite and single-slot path ownership.

## Structural correction

The successor keeps the source event and external objective, but defines the
execution structure on completed fifteen-second bars:

```text
five-candle local swing (radius two)
-> later displaced close through that swing
-> causal fifteen-second FVG
-> first valid FVG retest
-> first completed aggregate-trade second after the signal
```

Physical observation windows are preserved rather than silently shortened:

```text
displacement rank history     60 minutes = 240 fifteen-second bars
maximum MSS search             5 minutes = 20 fifteen-second bars
maximum first-retest search    5 minutes = 20 fifteen-second bars
```

The baseline waits for the first FVG retest. Its single controlled ablation
removes only the retest wait and enters at the completed displacement close.
