# Candidate 57 — public ichiV2 short lifecycle forensic v3

## Why this experiment exists

The verified finite-history campaign found a repeatable but incomplete short
family.  The frozen 30-day account completed 32 one-slot trades, earned about
5.0%, and had PF 1.44.  All 13 ROI exits were winners, while 19 source-signal
exits contained 17 losses.  The aggregate result does not tell us whether the
1.5-hour cross exit is the loss engine or whether it correctly terminates bad
entries before the 4% source stop.

No ROI, stop, fan, cloud, side, hold, or exit parameter is changed here.  The
finite-history implementation has already been proven trade-for-trade identical
to the full-history source implementation.

## Predeclared causal hypothesis

> The source exit is a valid thesis invalidation rather than the cause of the
> losses.  Trades that will reach the 1.5% ROI should build favourable excursion
> before the 5-minute trend crosses back over the 1.5-hour trend.  Source-exit
> losses should rarely recover to the original ROI before the original stop or
> 480-minute horizon after the exit signal.

If this is true, the next research problem is entry-state selection and
arbitration, not slower exit confirmation.  If false, the source exit is too
reactive and a minimal confirmation-state experiment is justified.

## Predicted observations

1. ROI winners should reach +0.25R before any source-exit cross and should spend
   most completed five-minute checks in the original short state.
2. Source-exit losses should have low pre-exit MFE and should lose the short
   source state before reaching +0.25R.
3. A non-trading post-exit shadow should hit the original ROI before the source
   stop or horizon in no more than a minority of source-exit losses.
4. The median post-exit shadow result should be worse than the actual source
   exit by a material amount; otherwise the exit is destroying recoverable
   trades.
5. The instrumented account must exactly reproduce the frozen 32-trade account.

## Falsification

The hypothesis is rejected when a large share of source-exit losses subsequently
reach the original ROI without touching the original stop, when their post-exit
shadow result is materially better than the actual exit, or when ROI winners and
source-exit losses have overlapping pre-exit path states.  In that case the next
experiment changes only exit confirmation and predicts exactly which recoveries
must be preserved.

## Recorded evidence

For every actual trade the wrapper records MFE, MAE, time to ±0.10R, ±0.25R and
-0.50R, active-source-state ratio, first source-state loss, first source-exit
cross, and the mark/MFE/MAE at that cross.  Every source-exit trade also spawns a
non-trading shadow using the original entry, 4% stop, 1.5% ROI and remaining
480-minute horizon.  Shadows never submit or modify account orders.

The June 2025 interval is already consumed and is used only for mechanism
diagnosis.  It cannot promote the family or serve as untouched evidence.
