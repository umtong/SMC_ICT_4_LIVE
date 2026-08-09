# Candidate 53 — State-first true-L1 OFI selector freeze

Frozen before opening 2024-03-18..2024-03-24.

This is a selector/mechanism test, not yet final execution geometry.  It is
specified from external microstructure results rather than from later-window
outcomes.

External evidence used:

- Bieganowski & Ślepaczuk (2026): OFI has a largely monotone return effect with
  saturation; wider spreads attenuate predictive effects; liquidity variables
  condition the usefulness of flow.
- Chang (2026): flow-adjusted near-touch absorption capacity is materially more
  informative than raw directional flow in BTC perpetual futures.
- Jeon (2026): pre-event liquidity state is first-order; order flow should be
  layered on top of state rather than used as the state itself.

## Frozen selector

Use the same causal approximately-30/day dollar-volume participation clock and
true Cont-style L1 OFI defined in `L1_OFI_FREEZE.md`.

A participation bar is eligible only when all of the following are true:

1. `abs(normalized true L1 OFI) >= its own trailing 90th percentile`.
2. **Price acceptance:** completed bar mid return has the same sign as OFI.
3. **Not-wide spread state:** quote-update-weighted mean spread of the completed
   bar is less than or equal to the median mean spread of the preceding 90
   completed participation bars.  This is a coarse state split, not an optimized
   threshold.
4. **Opposing queue depletion:**
   - for positive OFI / long direction, same-price ask quantity removed exceeds
     same-price ask quantity added over the completed participation bar, and
     ask price retreats are at least as frequent as ask price improvements;
   - for negative OFI / short direction, the symmetric condition is applied to
     the bid queue.

If any condition fails, classify the event as ABSORBED / FRAGILE / NO TRADE for
this continuation family.  No asset-name routing is permitted.

## Frozen diagnostic outcome

- direction: continuation in OFI sign;
- entry proxy: strictly next one-minute open after the participation bar;
- horizon: 240 minutes, retained from the already-frozen external OFI mechanism;
- round-trip hurdle: 21 bp;
- same-symbol diagnostic events are non-overlapping for 240 minutes.

No alternative quantile, spread percentile, queue ratio, direction, or horizon
may be chosen after viewing 2024-03-18..24.

## Data roles

- 2024-01-08..10: development/diagnostic only (already seen under earlier rules).
- 2024-03-18..24: untouched selector test at freeze time.
- If this selector passes strongly enough to justify execution work, reserve a
  later still-unopened March window before defining stop/target/management.

A chronological reconstruction of physically disordered Binance bookTicker ZIP
rows is a data-integrity repair and does not alter this economic policy.  Every
original timestamp, price, and quantity must be preserved exactly.
