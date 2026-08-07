# CVPD Research Ledger

## Hypothesis

BTCUSDT spot and USDT-M perpetual markets share the same underlying value but
contain different participant inventories.  A boundary break confined to the
perpetual and accompanied by an extreme perpetual/spot basis residual is more
consistent with leveraged inventory and liquidation pressure than broad price
discovery; a later reclaim is a reversal candidate.  Conversely, an accepted
spot boundary break while the perpetual has not yet confirmed is a price-
discovery relay candidate only after a separate perpetual catch-up response.

## State order

```text
PRIOR COMPLETED JOINT AUCTION
-> ONE-VENUE LIQUIDITY EVENT
-> PRIOR-ONLY BASIS CLASSIFICATION
-> RESPONSE OBSERVATION
-> PERPETUAL RECLAIM or PERPETUAL RELAY
-> STRUCTURAL TARGET / INVALIDATION / TIMEOUT
```

The initiating divergence bar cannot emit a trade.  Same-side breaks confirmed
by both venues are explicitly ambiguous and not traded.

## Predeclared variants

1. Full bifurcation: perpetual-only reversion and spot-led relay.
2. Perpetual-only false-break mechanism.
3. Spot-led relay mechanism.
4. Ineligible one-variable ablation without the robust basis gate.

Selection uses the first gate-qualified eligible mechanism in the fixed order
above, not the maximum first-week return.

## Invariants

- Exact equality of spot and perpetual completed one-minute timestamps.
- Current basis observation is excluded from its own median/MAD baseline.
- Current spot range and volume are excluded from their own baselines.
- Only BTCUSDT perpetual is traded.
- NautilusTrader handles every order, fill, position, fee, margin and NAV event.
- Whole-account NAV risk is fixed at three percent per approved trade.
- Across all markets, pending new entry plus open position remains at most one.
- The frozen weeks, costs, fill model and gates are unchanged.

## Failure classification

Missing spot data, timestamp mismatch, checksum failure, strategy construction,
Nautilus order/accounting failure or a missing metrics file is an implementation
or data failure and must be repaired before market interpretation.

With valid Nautilus metrics:

- no completed response means the causal event definition has insufficient
  opportunity density;
- negative cost-after expectancy means the bifurcation direction is wrong;
- a first-week pass followed by sealed-week failure is generalization failure;
- all three weeks are required before any long evaluation.
