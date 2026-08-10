# Candidate 57 — 4h jump management repair v2 freeze

## Research question

The v1 decomposition showed that the source 4h jump-reversion entry is not the only determinant of performance. Replacing a terminal one-minute stop with the completed four-hour impulse extreme improved the same ten November episodes from roughly -4.5R to near flat while converting two control losses into winners. The remaining loss engine contains several trades that first reached meaningful favorable excursion and then returned to a full stop or a negative horizon exit.

This experiment does not ask which parameter passes a scorecard. It asks whether a causal giveback-management module can remove repeated losses without destroying the rare large winners which constitute the alpha engine.

## Fixed development interval

`2025-11-01` through `2025-11-14`, four-symbol single continuous account. It was already opened by v1 and is therefore development data. All variants below are frozen together before this v2 run.

## Shared system

- BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT.
- One global pending entry or position.
- NautilusTrader matching, orders, portfolio and account accounting.
- Current NAV × 3% maximum planned loss, including modeled fees, slippage and funding reserve.
- Completed four-hour return, reversion direction, causal z-score from the preceding 18 completed four-hour returns, absolute threshold 2σ.
- Source-score arbitration.
- Structural stop beyond the full completed four-hour impulse extreme.
- Original source horizon remains four hours after the jump boundary.
- Protection uses the planned all-in loss per unit as R. A newly activated or ratcheted floor becomes usable only on the next completed minute; no same-bar retroactive fill is allowed.

## Variants

1. `impulse_control`: no additional protection.
2. `impulse_be_0p5`: after net favorable excursion reaches +0.5R, install an all-in break-even floor for subsequent minutes.
3. `impulse_lock_1p0_0p25`: after +1.0R, install a +0.25R all-in floor.
4. `impulse_trail_1p0_gap0p75`: after +1.0R, floor is the better of +0.25R or peak favorable R minus 0.75R, ratcheted only upward.
5. `confirm5_be_0p5`: retain the v1 post-jump five-minute rejection confirmation and add the +0.5R break-even floor. This distinguishes entry-state repair from management repair.

## Required decomposition

For every variant, inspect gross winner R/day, gross loss R/day, exact episode deltas, winner sign and R preservation, repeated loss rescue, winner truncation, protection exits, account-slot opportunity changes, symbol/side concentration, execution violations and drawdown. Net PnL is evidence but not the sole selector.

A management mechanism is worth freezing only when it preserves the observed payoff engine and improves more than one causal loss episode. If none does, the next action is not parameter rescue; it is a separate delayed extension-rejection/re-entry scenario built from the observed path anatomy.
