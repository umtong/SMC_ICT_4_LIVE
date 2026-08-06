# Candidate-02 v94 — Multi-level common accepted breakout

## Structural path from v93

v93's common spot-perpetual accepted breakout won, but the fixed three-cycle
registry supplied only one trade. v94 does not relax that acceptance logic. It
applies the same state to a larger causal registry of pre-existing liquidity:
completed 4-hour, 8-hour and previous UTC-day highs and lows.

## Pattern detectors versus scenario

A structural high or low, a breach, displacement or FVG is only a detector.
Trade authorization requires the ordered scenario:

`available intact level -> first breach -> common spot/perpetual acceptance -> limited basis expansion -> displacement/FVG -> accepted-side retest -> nearest intact external pivot`

Each underlying level is consumed once. Near-duplicate breached levels form one
cluster. A failed or ambiguous event does not get recycled into repeated signals.

## Causal level registry

* A four-hour level becomes available only after its four-hour range closes and
  expires after 24 hours.
* An eight-hour level becomes available only after its eight-hour range closes
  and expires after 48 hours.
* A previous-day level becomes available at the next 00:00 UTC and expires after
  72 hours.
* Levels already touched during warm-up are removed before evaluation.

## Acceptance and delivery

Within three completed minutes of the breach, at least two closes must remain
outside. Basis-adjusted spot must cross the equivalent level. Spot movement must
represent at least 25% of perpetual excess while basis expansion contributes at
most 75%.

A same-direction body above its shifted prior quantile and 0.20 prior ATR must
close beyond the level and leave a three-candle FVG. A later completed-minute
retest must touch the gap and close both past its midpoint and beyond the old
level. Trading 0.10 prior ATR back inside invalidates the setup.

## Target

The target is not a fixed R multiple. Before the event, confirmed five-minute
pivots are reconstructed from the preceding 48 hours. A pivot remains eligible
only when later pre-event closes have not passed it. The nearest pivot in the
trade direction whose cost-after reward/risk is at least 1.10 is selected.

## Prospective evaluation

BTC 2025-11-03 through 2025-11-10 UTC was selected before any v94 direct data
collection with seed `2026080694`. The central retest horizon is 20 minutes;
15 and 25 minutes are adjacent checks. Four-hour, eight-hour and previous-day
source-only runs diagnose concentration.

NautilusTrader 1.230.0 owns every order, fill, fee, position and NAV transition.
Risk is current NAV times 3% divided by expected per-unit loss including fees,
slippage, market impact and funding. No nominal cap or score multiplier exists.
