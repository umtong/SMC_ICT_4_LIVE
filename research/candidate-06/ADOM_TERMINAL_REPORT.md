# ADOM terminal report after controlled implementation repair

## Final classification

**Logical failure: negative cost-after expectancy.**

The first ADOM campaign mixed valid market metrics with a passive-fill OTO
rejection race.  That lifecycle defect was repaired without changing the signal,
entry origin, expiry, stop, target, fees, slippage, fill probability, risk,
week, or gates.  The identical first BTC week was rerun through NautilusTrader.
The runtime-error gate then passed, while the market result remained negative.

## Controlled result

Compared with the unchanged market-after-defense reference, passive entry at the
defense origin added six fills but reduced daily geometric NAV growth by
3.450593 percentage points, reduced win rate by 30.909091 percentage points,
reduced wins by one, reduced profit factor by 1.325426, and increased maximum
drawdown by 8.182370 percentage points.

The full variant submitted 13 entries; two expired unfilled.  It ended the week
at 83,700.8821 from 100,000, with only one positive day.  Its failures were
cost-after growth, win rate, positive-trade count and profit concentration.  No
unrelated runtime error remained.

## Failure cause

A revisit to the completed defense bar's origin was not evidence that the
accepted auction still controlled the next path.  The passive order admitted
deeper mitigations precisely when the structural path was often already
failing.  Three fills occurred on bars which had already crossed the structural
stop; these are now recorded as immediate stop/abort outcomes rather than engine
faults.

## Working components retained

- completed 30-minute auction construction;
- first held retest and separate directional-defense bar;
- causal fixed-auction order expiry;
- native GTD bracket and partial-fill single-slot fail-safe;
- positive but insufficient market-after-defense reference;
- recoverable drawdown in the full variant.

ADOM is discarded without tuning the origin price, lifetime, auction period,
stop, target, direction, session, risk or costs.
