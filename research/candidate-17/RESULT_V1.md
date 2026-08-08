# Candidate 17 v1 — official first screen

## Frozen evaluation

- BTCUSDT perpetual
- build/bootstrap: 2023-12-22 through 2023-12-31 UTC
- evaluation: 2023-12-25 through 2023-12-31 UTC
- continuous starting NAV: 100,000 USDT
- current-NAV planned risk: 3% maximum per trade
- realistic registered costs and NautilusTrader execution
- workflow run: `31248421806`
- evaluated commit: `3efdf932d37bb997cff95404fb40ee7026a58325`

## Result

- trades: 11
- wins/losses: 3 / 8
- win rate: 27.2727%
- profit factor: 0.409435
- ending NAV: 90,064.887554
- total return: -9.935112%
- geometric daily growth: -1.483737%
- maximum drawdown: 16.6281%
- liquidations: 0
- order rejections: 3
- gate: failed

## State attribution

- rejection trades: 10, net -8,205.64 USDT
- acceptance trades: 1, net -1,729.47 USDT
- remembered defenses: 165
- reattack observations: 323
- depletion confirmations: 0
- clean first-defense reversals: 127 observed, 10 actually entered

The new depletion branch was not responsible for the loss because it produced no entries. The inherited immediate-entry clean-failure branch was the dominant loss source.

## Structural failure findings

1. A confirmed opposite initiative was treated as an entry rather than evidence. Several losing entries were stopped or fail-closed within minutes, while the winning paths generally required much longer development.
2. Many losing brackets had execution costs larger than the structural price distance to invalidation. Risk sizing then created very high notional exposure and made a small adverse fill consume a large fraction of NAV.
3. One market fill opened beyond its planned stop, so the attached stop child was rejected. Candidate 16 v2 fail-closed the position, but the run correctly failed execution integrity.
4. Positive five-minute OI and positive top-book imbalance were not universal depletion evidence. Their conjunction suppressed every repeated-defense continuation.

## V2 decision

V1 is not advanced unchanged. V2 makes two structural changes without fitting a PnL threshold:

- later initiative arms the first retest; it never enters on the initiative bar;
- structural invalidation distance must be at least as large as the complete expected execution-cost component.

The first screen is now development evidence and remains permanently recorded rather than overwritten.
