# Portfolio SCDAM extension

This extension is activated only after a valid BTC W1 Nautilus run shows positive after-cost expectancy but insufficient independent opportunities. It does not relax the BTC thresholds.

## Market logic

Every symbol uses the same New-York/DST-aware completed-session auction map. Source ranges are frozen before their target session. A boundary trade-through starts a price-discovery episode; it is never an entry by itself.

### FAR

- final raid extreme and aggregate aggressive-flow absorption;
- range reclaim;
- a new post-raid causal internal pivot;
- displacement through that pivot with reversed flow;
- first retracement into the execution void;
- stop beyond the final raid extreme and target at confirmed opposing external liquidity.

### AAC

- repeated closes and progress outside the source boundary;
- causal pullback retaining at least half of the impulse;
- frozen pre-pullback impulse extreme;
- re-acceleration through that extreme with aligned flow;
- boundary-backed stop and next confirmed external-liquidity target.

FAR and AAC keep separate extremes and invalidation state.

## Four-market arbitration

BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT may emit candidates at the same completed minute. Selection is deferred until all symbols at that timestamp are observed, eliminating subscription-order bias. Priority is:

1. lowest calibrated error bound, when available;
2. highest after-cost structural R;
3. lowest expected loss fraction of entry price;
4. earliest causal observation;
5. deterministic symbol and scenario tie breakers.

From entry submission through cancellation/expiry or final position closure, the global mutex is occupied. Across all four symbols:

```text
pending new entry orders + open positions <= 1
```

Exit/reduction orders do not count as new entries.

## Risk and execution

The selected candidate keeps the project risk rate of exactly 3% of current account NAV. Candidate rank never changes quantity. The parent is a post-only GTD limit order, target is maker limit, and protection is stop-market. Fees, adverse slippage, impact reserve and funding reserve are included in expected per-unit loss before quantity is calculated. If the exact risk-sized quantity is not feasible under actual venue margin/minimum rules, the trade is rejected rather than clipped by a discretionary exposure limit.

## Evidence rule

Signal-only target-first outcomes do not authorize portfolio deployment. The portfolio extension must generate non-empty Nautilus order, position and account reports and pass the independent evidence audit on the unchanged frozen W1 before W2 is eligible.
