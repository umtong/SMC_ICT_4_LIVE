# ADOM post-evidence execution rejection hardening

## Classification

The first completed ADOM campaign produced valid NautilusTrader portfolio and
trade metrics, but also recorded nine late order rejections. The recurring
sequence was:

1. a passive defense-origin parent filled while the same bar had already moved
   through the structural stop;
2. the newly released OTO stop child was rejected as already in the market;
3. the partial-entry single-slot fail-safe was already canceling and flattening;
4. a contingent child cancellation and a duplicate reduce-only flatten could
   then be rejected after their object or position was already terminal.

This is an **execution lifecycle implementation defect**, not evidence that the
market scenario worked. It also does not rescue the candidate's negative
cost-after expectancy.

## Repair boundary

The repair changes no scenario detector, entry origin, expiry, stop, target,
fee, slippage, fill probability, risk fraction, data, frozen week, or gate.

Only three deterministic late-rejection classes are handled specially:

- `STOP_MARKET ... was in the market` for a filled defense-origin entry becomes
  an immediate stop/abort outcome and triggers at most one flatten request;
- `Contingent order ... already closed` during that abort is diagnostic;
- a reduce-only flatten rejected after the portfolio is already flat is
  diagnostic.

All other protective-order denials or rejections remain runtime errors and force
an emergency exit.

## Decision after rerun

The identical first BTC week must be rerun. If runtime errors are gone and the
full ADOM variant still fails its frozen market-performance gates, ADOM is a
logical failure and is discarded without parameter rescue. If any unrelated
order error remains, it is still an implementation failure and must not be
interpreted as strategy performance.
