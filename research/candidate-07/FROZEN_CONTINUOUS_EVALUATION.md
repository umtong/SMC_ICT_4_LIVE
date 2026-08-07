# Preregistered first continuous evaluation

This period is committed before the preregistered Week 2 result is known. It is
executed only if both frozen validation weeks pass their context-identity and
performance gates. It may not be replaced because of trade count, volatility,
performance, archive convenience, or regime labels.

## Frozen candidate source

`89e03f9314eab9a456c3fe1cb0d08b2a1190dba6`

## Mechanical selection

- candidate block length: 28 consecutive UTC days
- first eligible Monday: `2024-01-01`
- last eligible Monday: `2025-11-17`
- eligible starts: `99`
- fixed label: `frozen-continuous-evaluation-28d`
- digest input:
  `89e03f9314eab9a456c3fe1cb0d08b2a1190dba6:frozen-continuous-evaluation-28d`
- SHA-256:
  `2a16ef5a965b7fa490200e9817cbd0cbc3dbec9e3a89043d2e86b501d1fc9153`
- first 64 bits as unsigned integer: `3032874571429281700`
- index modulo 99: `55`

## Frozen interval

- start: `2025-01-20`
- end exclusive: `2025-02-17`

The interval does not overlap the development week or either preregistered
validation week.

## Execution contract

The evaluation must use:

- one NautilusTrader `BacktestEngine`;
- one shared USDT margin account;
- unchanged BTCUSDT and XRPUSDT multiclock first-retest logic;
- current total NAV for every 3% planned-loss quantity calculation;
- one portfolio-global pending/open slot;
- the same taker fees, adverse ticks, funding reserve, actual funding replay,
  cost-viable MIT target, structure stop, and 30-minute maximum hold;
- continuous strategy and account state from the first to the final event;
- manual Nautilus streaming batches if required for memory, without resetting
  the engine, strategies, account, liquidity ownership, or portfolio slot.

Independent daily or weekly equity curves may not be multiplied or concatenated.
The run is diagnostic even if successful; it is not a live-deployment approval.
