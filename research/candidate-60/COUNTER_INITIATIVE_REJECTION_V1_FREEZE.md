# Candidate 60 — frozen common-factor-owned failed counter-initiative diagnostic

## Why this hypothesis exists

The prior seesaw-continuation experiment did not fail only because its aggregate
return was negative. The strongest peer counter-initiatives — observations in
which both peer price movement and peer aggressor-flow magnitude exceeded their
strictly prior daily medians — lost in the proposed continuation direction in
all six development events. Fading those six bursts produced a positive mean
only because one unusually large event dominated the result; removing that
winner made the fade negative after costs.

That evidence does not authorize an immediate fade or a stronger threshold. It
changes the state model. A strong peer move opposite a persistent large-coin
leader can be either:

- genuine capital rotation accepted by the market;
- a temporary countertrend inventory burst inside a dominant common-factor
  move;
- a mechanical liquidity event whose impact later decays;
- an idiosyncratic move unrelated to the selected leader.

The missing observation is whether the peer initiative is accepted or rejected.
The new policy therefore waits for a later, separate failure observation. It is
a new failed-counter-initiative hypothesis, not a parameter repair of seesaw V1.

## Economic mechanism

Crypto large-cap returns contain a strong common component. A broad initiative
can be driven by correlated information, leverage adjustment, hedging, or
market-wide risk transfer. During that initiative, one peer can temporarily
trade in the opposite direction because local liquidity takers, profit taking,
or inventory relief absorb the common move.

The opposite burst is not automatically trapped. It becomes a candidate failed
initiative only when:

1. the broad common factor and its strongest leader were already aligned;
2. the leader remained aligned while the peer launched a strong opposite burst;
3. on a later completed observation, the peer's price and aggressor flow both
   turned back toward the common factor; and
4. the later price response reclaimed the midpoint of the counter-burst body.

Only after this state transition is the peer traded toward the common factor.

## Data and causal normalization

Checksum-verified Binance Vision USD-M perpetual one-minute klines are used for
BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT.

For each completed block:

`signed aggressor quote = 2 * taker_buy_quote - total_quote`

`flow imbalance = signed aggressor quote / total_quote`

Price return and flow magnitude are normalized with strictly prior medians:

- parent: preceding 96 complete 15-minute blocks, minimum 48;
- counter-burst: preceding 288 complete 5-minute blocks, minimum 144;
- each rolling baseline is shifted one block so the current observation is not
  included.

No symbol, clock, side, absolute return, volume, volatility, or fitted z-score
threshold is used.

## Frozen proposed state sequence

At each completed 15-minute parent boundary:

1. compute the cross-sectional median return and median flow imbalance across
   the four assets;
2. both medians must have the same nonzero sign; this is the common-factor
   direction;
3. an eligible leader must have price and flow in that direction, with both
   return and flow magnitude above its strictly prior daily median;
4. choose the eligible leader with the largest product of parent return ratio
   and flow ratio; ties use BTC, ETH, SOL, XRP.

During the immediately following completed five-minute block:

5. the leader's price and aggressor flow must remain in the common-factor
   direction;
6. an eligible peer must have price and flow in the opposite direction;
7. both peer counter-burst return and flow magnitude must exceed their strictly
   prior daily medians;
8. choose the eligible peer with the largest product of counter return ratio and
   flow ratio; ties use the same fixed symbol order.

During the immediately following completed one-minute block:

9. the peer's price return and aggressor flow must both turn into the
   common-factor direction;
10. the peer close must cross the arithmetic midpoint of the completed
    counter-burst open and close in the common-factor direction.

The trade is entered in the peer at the next one-minute open, in the
common-factor direction. This entry uses no information from the entry minute.

## Peer-only causal control

The same diagnostic also records a generic failed-initiative control:

```text
strong 5m price-and-flow initiative in one asset
→ later completed 1m price-and-flow reversal
→ counter-body midpoint reclaimed
→ next-open trade opposite the original initiative
```

This control has no cross-asset leader or common-factor requirement. It is not a
competing strategy selected after the result. Its purpose is attribution: if the
leader-owned family does not improve upon the peer-only failed-initiative
process, the cross-asset explanation has not added information.

The exact opposite direction is also retained for every event.

## Outcomes, cost, and one-slot path

- outcome opens: 5, 15, 30, and 60 minutes after entry;
- **15 minutes is the sole primary horizon**;
- gross return is signed in basis points;
- every event subtracts a frozen 20 bp round-trip friction floor;
- simultaneous events choose the largest fully pre-entry state strength, with a
  fixed symbol tie-break;
- selected events are non-overlapping for each horizon.

This is an opportunity-set diagnostic. It is not a NautilusTrader NAV claim.
Promotion would authorize a later structural-stop, target, sizing, and one-slot
Nautilus implementation only.

## Frozen periods

Development:

- scored entries: **2026-07-20 through 2026-07-26 UTC**;
- two prior days are downloaded for causal baselines;
- one following day is downloaded for outcomes.

Conditional policy-fresh:

- scored entries: **2026-08-03 through 2026-08-09 UTC**;
- downloaded only if the frozen development conditions are all met;
- this interval was not consumed by seesaw V1, spot/perpetual price discovery,
  or impact-efficiency experiments.

The development period is new for this exact policy. Any result observed there
becomes development evidence and cannot be called holdout evidence.

## Fresh-data authorization conditions

At the primary 15-minute horizon, fresh data may be consumed only if:

1. at least seven one-slot proposed events complete in seven scored days;
2. proposed mean and cumulative net-after-20-bp return are positive;
3. at least two target assets have positive mean net return;
4. at least three scored days have positive day-level mean net return;
5. the proposed direction beats the exact opposite direction;
6. removing the best proposed event leaves positive cumulative net return; and
7. the leader-owned proposed mean net return exceeds the peer-only control mean
   net return.

These conditions protect untouched data; they are not a binary claim that every
failed condition makes every component useless. All wins, losses, no-trades,
tail dependence, symbols, directions, leader/peer pairs, and controls must be
analyzed before deciding what to preserve.

## Anti-fitting contract

No threshold, baseline, midpoint definition, horizon, side, symbol, date, or
clock subset is changed after the development outcome. Adjacent horizons cannot
rescue a failure at 15 minutes. A positive aggregate result does not authorize
promotion when it is caused by one trade, one symbol, one day, the opposite
placebo, or a peer-only effect.
