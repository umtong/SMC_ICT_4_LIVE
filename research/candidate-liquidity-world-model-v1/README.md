# Candidate Liquidity World Model V1

This branch continues the liquidity-auction research without treating the V1–V7
policies as a benchmark to preserve.

## What is reused

- V5 point-in-time price, volume, derivatives and common-market preparation;
- V5 pending first-return and TP/SL-only economic assumptions;
- semantic horizontal liquidity and the V7 volatility-normalized directional-change hierarchy;
- the existing chart export path and GitHub research image.

## Structural change

The research unit is one causal market episode, not a grid of plan variants.

```text
pre-existing liquidity world model
-> failed auction / accepted auction / initiative mitigation episode
-> completed price-volume control evidence
-> one origin zone
-> one structural invalidation
-> one fresh destination
-> one actual pending order
```

The destination is selected before reward/risk is calculated. There is no fixed
1R–2R target lattice and no hindsight `best_plan` label. If the real destination
does not pay at least 1.0 gross R, the episode is not traded.

Each episode can create at most one order. Across BTCUSDT, ETHUSDT, SOLUSDT and
XRPUSDT, one global pending-order/position slot is applied. An unfilled order may
be canceled when the original first-return opportunity dies; after fill, only the
predeclared take-profit or stop-loss can close the position.

## Initial diagnosis

`research-liquidity-world-model-short.yml` runs several separated one-week market
regimes to expose implementation and market-logic errors cheaply. It publishes the
actual account trades and the largest missed no-trade episodes so the next change is
made from trade/no-trade chart evidence rather than aggregate metrics alone.
