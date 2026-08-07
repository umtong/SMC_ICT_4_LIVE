# AFIB — Adaptive Flow-Impact Bifurcation

## Research question

Can a completed aggressive-order-flow shock be traded only after price reveals
whether liquidity was consumed efficiently or replenished strongly enough to
absorb the shock?

The engine does not assume that high signed flow is bullish or bearish by
itself. It separates two causal paths:

```text
prior-only robust flow baseline
→ completed signed-flow surprise with elevated activity
→ realized price-impact classification
→ separate completed response
→ continuation or reversal entry
```

### Efficient-impact path

A flow surprise moves price in the same direction with directional body and an
extreme close. A later completed minute must break the shock extreme with
aligned aggressive flow. The target is the nearest cost-worthy prior liquidity
objective or the completed shock-range extension.

### Absorbed-impact path

A flow surprise produces weak or opposite signed price impact, an adverse wick,
or a non-extreme close. A later completed minute must cross the shock midpoint
with opposite aggressive flow. The stop remains beyond the shock extreme and
the target is prior-side liquidity.

## Why this is structurally preferable to the failed first-week winners

Earlier HFF/HML/SIAR first-week winners depended on a higher-timeframe accepted
bias. Their frozen second week repeatedly entered many shorts while the market
was repricing upward, and the same signal family collapsed. AFIB removes that
persistent directional label. Direction is decided at each local liquidity
shock by the joint state of aggressive flow and realized price impact.

The design follows three established microstructure observations:

1. Short-horizon price changes are more closely related to order-flow imbalance
   than to raw traded volume, and impact varies inversely with available depth
   (Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*, 2014,
   DOI 10.1093/jjfinec/nbt003).
2. Extreme order-sign imbalance can coincide with pinned prices rather than
   large returns, so flow magnitude must be interpreted jointly with realized
   impact (Patzelt & Bouchaud, *Universal scaling and nonlinearity of aggregate
   price impact in financial markets*, 2018, arXiv:1706.04163).
3. Price impact contains transient mechanical and more persistent informational
   components; interruption or reversal of flow can produce mean reversion
   (Donier, Bonart, Mastromatteo & Bouchaud, *A fully consistent, minimal model
   for non-linear market impact*, 2015, arXiv:1412.0141).

## Fixed causal and execution contract

- Binance USD-M `aggTrades` are checksum verified.
- Each minute is visible only at its completed timestamp.
- Median/MAD flow and activity baselines exclude the current minute.
- The initiating shock cannot emit an order.
- A distinct completed confirmation minute is mandatory.
- BTCUSDT perpetual is the only traded instrument.
- NautilusTrader owns replay, orders, fills, fees, positions, margin and NAV.
- Approved trades use three percent of whole-account NAV as planned loss,
  including entry/stop fees and one-tick adverse execution.
- No session blacklist, long/short performance filter, nominal cap or score
  multiplier is introduced.

## Staged decision

Only a first-week gate-qualified mechanism is locked. The locked configuration
is replayed unchanged on the second frozen BTC week, and only a pass opens the
third week. Long evaluation remains sealed until all three weeks pass.
