# Candidate-07 discarded implementation: five-minute valuation confirmation geometry

## Hypothesis retained, implementation discarded

The candidate measured:

```text
valuation anchor = sum_open_interest_value / sum_open_interest
basis            = (trade close - valuation anchor) / valuation anchor
```

A five-minute tail basis event with aligned aggressor flow and a non-neutral OI impulse was followed for up to six completed five-minute bars. A trade required measurable basis contraction, opposite aggressor flow and price reversal. Direction came from basis sign, not OI sign.

The economic hypothesis—trade price can temporarily dislocate from a contemporaneous derivatives valuation anchor and later contract—was not rejected by this run. The implementation that used the **same five-minute confirmation bar** to define entry, stop and remaining fair-value target was rejected.

## Frozen Week-1 diagnostic

```text
five-minute tail dislocations             26
contraction + counterflow confirmations   13
trade plans                                0
Nautilus positions                         0
```

All 13 confirmed events were causally evaluated. Exact geometry reasons:

```text
fair value already passed                  5
remaining RR below 1.25                    8
nonpositive risk                           0
accepted geometry                          0
```

For the eight events where fair value remained ahead:

```text
minimum uncapped RR   0.00936
median uncapped RR    0.04585
maximum uncapped RR   0.14212
```

The confirmed basis deviations generally left only about 1–9 USDT of remaining fair-value movement, while a five-minute confirmation-bar stop required tens to hundreds of USDT. This was not a fee, slippage or Nautilus fill problem; no order reached the engine because the signal-time geometry was structurally invalid.

## Largest performance driver

The dominant factor was **time-scale mismatch**:

```text
valuation dislocation magnitude: a few basis points
five-minute confirmation risk:   a full five-minute auction range
```

Lowering `minimum_rr`, reducing a numerical stop buffer, or extending the target beyond measured fair value would force a backtest trade without resolving that mismatch. Those changes were therefore not attempted.

## Valid components retained

- direction from actual price–valuation deviation rather than OI sign;
- past-distribution tail classification with tie-safe percentile rank;
- explicit normalization before a new episode;
- contraction and counterflow required before entry;
- fair-value-first events terminated without a late trade;
- fixed target at `ENTRY_READY` and delayed-entry RR erosion rejection;
- checksum positioning data and gap-safe OI handling;
- Nautilus current-NAV 3% sizing, fees, adverse ticks and funding reserve.

## Structural correction

The next implementation separates the clocks:

```text
five-minute:
  OI snapshot + index basis tail + signal aggressor flow

one-minute:
  exact Binance index-price basis contraction
  + opposite aggressor flow
  + one-minute structure stop
```

The official one-minute `indexPriceKlines` archive is checksum verified and joined exactly to the completed one-minute trade bars. No index value is interpolated or forward-filled. If fair value is reached before a valid one-minute entry, the episode terminates without a trade.

This correction keeps the causal hypothesis while replacing the invalid five-minute execution geometry. It is not a threshold adjustment or an attempt to manufacture trades.
