# Candidate 08 mechanical strategy specification

## 1. Market hypothesis

Stop orders and breakout participation cluster near visible extrema. A trade through one of those
levels can produce either of two economically distinct states:

1. liquidity is consumed but price is not accepted outside the prior range, so trapped breakout
   inventory and exhausted aggressive flow support a reversal;
2. aggressive flow displaces price through the level and passive liquidity relocates behind it, so
   a held retest supports continuation to the next pool.

The strategy therefore predicts a sequence, not a candle name:

```text
causally confirmed pool -> interaction -> rejection or acceptance -> independent confirmation
-> structural invalidation and external target -> risk-sized OUO bracket
```

The implementation uses one-minute OHLCV proxies for the first screen. It does not claim that bar
volume is equivalent to order-flow imbalance or depth replenishment. A later tick/order-book
implementation is warranted only if this coarse causal form already shows strong cost-after alpha.

## 2. Pattern detector

### Confirmed swing liquidity pool

A high/low pivot requires three closed bars on each side. The visual pivot timestamp is retained as
`event_time_ns`; it becomes tradable only at the third right-hand bar close, stored separately as
`observed_time_ns`. Equal or nearby pools within 0.12 ATR merge and increase `touches`. Pools expire
after 1,440 bars.

### Interaction

A high pool is interacted with only when the previous close was at/below it and the new bar trades at
least 0.04 ATR above it. Low pools are symmetric. Penetration above 1.8 ATR is treated as an already
expanded move, not a sweep entry. A bar interacting with both sides is explicitly unresolved.

### Rejection arm

For a high pool, the bar must close at least 0.02 ATR back below the level, have upper-wick/body at
least 0.55, range at least 0.55 ATR, and volume at least 0.75 times its rolling median. Low rejection
is symmetric. Rejection is only **armed** at this point.

Within three subsequent bars, the strategy requires a directional close through the interaction
bar midpoint by another 0.08 ATR with the correct candle direction and close location. This is the
mechanical CHoCH/displacement confirmation. No trade is emitted before it.

### Acceptance arm

For a high pool, the interaction bar must close at least 0.12 ATR above the pool, have body at least
0.50 ATR, close in the upper 32% of its range, and carry at least 0.95 times median volume. Low
acceptance is symmetric.

Within ten later bars, price must retest within 0.22 ATR of the pool without closing more than 0.38
ATR through the wrong side; the confirming close must hold at least 0.01 ATR outside the pool with a
directional close location. This state is a BOS acceptance and first held retest, not a breakout
chase.

## 3. Entry, invalidation, and target

Entry is a market order submitted only after the independent confirmation bar has closed. The fill
model charges an adverse tick. The hard stop is outside the most extreme interaction/retest price
plus 0.10 ATR and is never closer than 0.18 ATR from the signal close.

The first target is the nearest still-active opposite-side liquidity pool that supplies at least the
minimum gross structural reward. If none exists, the target is a 0.786 projection of the causally
known dealing range, but never closer than the minimum structural reward. The adapter recomputes
**cost-after** reward/risk after tick rounding and rejects any setup below 1.20.

No fixed profit cap or trailing stop is used. The position exits only by:

- target order;
- structural stop order;
- 180-bar scenario timeout;
- evaluation-window flattening;
- emergency close after an execution failure.

## 4. Quantity

For current total USDT NAV `A_t` and fixed risk fraction `rho=0.03`:

```text
planned loss budget = A_t * rho
per-unit expected loss = |expected entry - stop| + both fill fees + two adverse ticks
quantity = floor_to_venue_increment(planned loss budget / per-unit expected loss)
```

The rounded planned loss is asserted to remain at or below 3% of NAV. Strategy confidence never
changes the fraction.

## 5. State/event diagnosis

Every pool and scenario records a causal state chain. Scenario outcomes are joined to Nautilus
positions through the opening order ID. Diagnostics separate:

- rejection vs acceptance PnL;
- pool/confirmation timeouts and pre-entry invalidations;
- cost-after geometry skips;
- target, stop, timeout, execution-error, and unexpected/liquidation closes;
- trade and week concentration;
- residual orders/positions;
- data gaps and source hashes.
