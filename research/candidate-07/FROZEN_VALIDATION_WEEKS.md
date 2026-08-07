# Frozen validation-week selection for the BTC/XRP multiclock portfolio

This file is committed before either validation week is executed. The dates may
not be replaced after observing data availability, trade counts, or performance.
A data-contract failure is recorded as such; it does not authorize selecting a
new week.

## Frozen candidate source

`89e03f9314eab9a456c3fe1cb0d08b2a1190dba6`

This source contains the unchanged multiclock first-retest detector, the
BTCUSDT/XRPUSDT one-engine portfolio runner, and the portfolio-global one-slot
execution contract which passed the development week.

## Mechanical selection universe

- first Monday: `2024-01-01`
- last eligible Monday: `2025-12-15`
- spacing: seven calendar days
- eligible Monday count: `103`
- each evaluation is `[selected Monday, selected Monday + 7 days)`
- development week `2025-12-22` is outside the selection universe

For each fixed label:

1. compute `SHA256(frozen_source + ":" + label)`;
2. interpret the first 16 hexadecimal characters as an unsigned big-endian
   64-bit integer;
3. take the integer modulo `103`;
4. use that zero-based Monday index.

No market data, strategy result, volatility statistic, symbol return, or trade
count enters the selection.

## Frozen Week 2

- label: `frozen-validation-week-2`
- SHA-256:
  `3a6cecdb13995f4e37a0e32506f496a6ba062a0a27b2be639882f0f7c0d0f9b0`
- first 64 bits: `4210000177355382606`
- index: `82`
- start: `2025-07-28`
- end exclusive: `2025-08-04`

## Frozen Week 3

- label: `frozen-validation-week-3`
- SHA-256:
  `de71b969fbabf4b604968b86b1db121e94b724c65206a30377f73358ab6760dc`
- first 64 bits: `16028796413633361078`
- index: `59`
- start: `2025-02-17`
- end exclusive: `2025-02-24`

## Promotion order

1. The development-week event-context identity audit must pass first.
2. Week 2 is then run from the exact frozen source with no code, symbol,
   parameter, risk, target, or execution change.
3. Week 3 is run only if Week 2 passes the existing weekly gate.
4. Passing both weeks authorizes a continuous longer evaluation; it does not by
   itself authorize live deployment.

The weekly gate remains the one serialized by the frozen runner:

- after-cost daily geometric NAV growth at least 1%;
- at least seven completed trades;
- at least four active days;
- maximum drawdown no greater than 20%;
- largest winning-trade share no greater than 55%;
- positive NAV and one portfolio-global pending/open slot.
