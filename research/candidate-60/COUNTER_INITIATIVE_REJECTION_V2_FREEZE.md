# Candidate 60 — frozen delayed factor-owned counter-initiative rejection V2

## Structural origin

V1 did not produce a losing proposed account; it produced no proposed trades.
The causal funnel showed that the intended state was not absent:

- 142 parent events retained a strong leader through the next five-minute block;
- 9 of those contained a strong opposite peer price-and-flow burst;
- 6 of the 9 later completed the full price-and-flow midpoint rejection;
- none completed that rejection in the immediately following minute;
- first completion occurred after 2, 2, 2, 3, 5 and 6 minutes.

The V1 error was therefore a transition-time assumption. V2 does not lower the
leader or peer thresholds and does not reverse the strategy because a placebo
looked better. It keeps the same economic mechanism but permits an explicitly
`UNRESOLVED` auction before entry.

## Economic hypothesis

A broad large-coin initiative represents a common market factor. A different
asset can temporarily move against that factor because of local profit taking,
liquidity taking, hedging, or inventory relief. The opposite burst is not
automatically trapped and is not faded immediately.

It becomes a candidate failed counter-initiative only when:

1. the original broad factor and selected leader were price-and-flow aligned;
2. the leader remained aligned while the peer launched a strong opposite
   five-minute price-and-flow burst;
3. on a later completed one-minute observation, the peer's price and aggressor
   flow both turned back toward the original factor;
4. that later close reclaimed the arithmetic midpoint of the counter-burst
   body; and
5. at least two of the other three large assets simultaneously retained
   price-and-flow ownership of the original factor direction.

The last condition prevents a stale leader label from converting a genuine
market-wide reversal into a peer-specific rejection trade.

## Data and immutable features

Universe:

- BTCUSDT
- ETHUSDT
- SOLUSDT
- XRPUSDT

Source: checksum-verified Binance Vision USD-M perpetual one-minute klines.

For every completed block:

`signed aggressor quote = 2 * taker_buy_quote - total_quote`

`flow imbalance = signed aggressor quote / total_quote`

Prior-only normalisation:

- parent: preceding 96 complete 15-minute blocks, minimum 48;
- counter-burst: preceding 288 complete 5-minute blocks, minimum 144;
- each baseline is shifted by one complete block before rolling.

No symbol, clock, side, absolute return, volume, volatility, or fitted z-score
threshold is used.

## Frozen proposed state machine

At every completed 15-minute parent boundary:

1. compute the cross-sectional median return and median flow imbalance across
   the four assets;
2. both medians must have the same nonzero sign; this is the common-factor
   direction;
3. an eligible leader must have return and flow in that direction, with both
   return magnitude and flow magnitude above its strictly prior median;
4. choose the eligible leader with the largest product of its two prior-normalised
   magnitudes; ties use BTC, ETH, SOL, XRP.

In the immediately following completed five-minute block:

5. the selected leader's price and flow must remain in the factor direction;
6. an eligible peer must have price and flow in the opposite direction, with
   both magnitudes above its strictly prior median;
7. choose the strongest eligible peer with the same deterministic rule.

The peer is then `UNRESOLVED` for at most ten completed one-minute observations,
representing two complete five-minute auction lengths. For each completed minute
in order:

8. peer price and aggressor flow must both turn into the original factor
   direction;
9. the peer close must cross the counter-burst body midpoint in that direction;
10. among the other three assets, at least two must have that minute's price
    return and aggressor flow both aligned with the original factor.

The first minute satisfying all three conditions completes the rejection.
Entry is the peer's next one-minute open in the original factor direction. If no
such minute appears within ten observations, the episode expires as
`UNRESOLVED / NO TRADE`.

## Attribution controls

### Peer-only delayed rejection

The same source records:

```text
strong single-asset 5m price-and-flow burst
→ within ten minutes, first opposite price-and-flow turn
→ body midpoint reclaimed
→ next-open fade
```

This control has no common factor, leader, or factor-ownership requirement. It
measures whether the proposed cross-asset explanation adds information beyond a
generic delayed failed initiative.

### Exact opposite direction

Every proposed event retains the exact opposite signed return with identical
entry timestamp, horizon and cost. A result that works only in the opposite
placebo direction does not validate the proposed mechanism.

## Outcome and one-slot contract

- diagnostic outcomes: 5, 15, 30 and 60 minutes after entry;
- **15 minutes is the sole primary horizon**;
- 20 bp round-trip friction is subtracted from every event;
- simultaneous events choose the largest fully pre-entry state strength using a
  fixed symbol tie-break;
- selected events are non-overlapping for each horizon.

This remains an opportunity-set diagnostic, not a NautilusTrader NAV claim.
Promotion would only authorize a later implementation with structural
invalidation, causal target, current-NAV 3% sizing, realistic fills and the
four-asset one-slot account.

## Frozen periods

Development:

- scored entries: **2026-06-29 through 2026-07-05 UTC**;
- two prior days are downloaded for causal baselines;
- one following day is downloaded for outcomes.

Conditional policy-fresh:

- scored entries: **2026-08-03 through 2026-08-09 UTC**;
- consumed only if all development authorization conditions are met.

The new development period was not used to select V2's direction, symbols,
thresholds, maximum wait, primary horizon or cost. Any V2 observation there is
development evidence from that point onward.

## Fresh-data authorization

At the primary 15-minute horizon, fresh data may be consumed only if:

1. at least five one-slot proposed events complete;
2. mean and cumulative net-after-20-bp returns are positive;
3. at least two target assets have positive mean net return;
4. at least three scored days have positive day-level mean net return;
5. the proposed direction beats its exact opposite;
6. cumulative net return remains positive after removing the best event; and
7. factor-owned mean net return exceeds peer-only delayed-rejection mean net
   return.

These conditions protect untouched data. They are not a substitute for
trade-by-trade diagnosis. A positive aggregate result is not accepted when it is
caused by a single trade, one symbol, one day, one direction, stale factor
ownership, or a peer-only effect. A negative aggregate result does not erase a
correctly identified transition component; each causal stage is analysed before
retirement or reuse.

## Anti-fitting contract

After the development result, no wait length, ownership count, threshold,
baseline, midpoint, side, symbol, date, horizon, or cost is changed on the same
period. Adjacent horizons cannot rescue the primary horizon. Any structural
revision must state what V2 misunderstood and move to new development data.
