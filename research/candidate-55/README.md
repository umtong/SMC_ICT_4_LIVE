# Candidate 55 — source-faithful 1h Picasso tournament

Candidate 55 starts from the highest-information external claim available in the inherited research: the public `RSI_BB_MACD_Nov_2023_1h_2_Dec` futures strategy.  The source reports very high trade frequency and daily profitability over more than 1,000 days, but those figures used a broad asset universe, multiple simultaneous positions and Freqtrade-specific execution.  They are discovery signals, not project evidence.

## Reused foundation

Candidate 55 fast-forwards from Candidate 51 and reuses its:

- NautilusTrader `BacktestNode` execution and matching;
- BTCUSDT/ETHUSDT/SOLUSDT/XRPUSDT data contracts;
- one global pending-entry/position slot;
- continuous NAV, fees, adverse slippage and funding reserve;
- exact current-NAV 3% planned-loss sizing;
- public RSI/BB/MACD/ADX indicator and exit implementation.

No account, portfolio or matching engine is recreated.

## Missing semantic repaired here

The inherited Picasso adapter tested 15-minute and 5-minute interpretations and correctly separated Python's original operator-precedence behavior from intended parenthesization.  It nevertheless converted the public strategy's entry **level** into a one-time rising edge.  In Freqtrade, a flat pair can enter again on a later source candle while the entry condition remains true.  This distinction is material for the strategy's claimed frequency, especially because the original precedence bug makes its first ADX branches independent of trend and volume.

Candidate 55 therefore predeclares a direct tournament:

1. `exact_level` — original precedence plus source-compatible level re-entry;
2. `exact_level_short` — same, preserving the source report's strongly short-dominant behavior;
3. `exact_edge` — original precedence with deduplicated rising edges;
4. `corrected_level` — intended grouping with level re-entry;
5. `corrected_edge` — intended grouping with rising edges.

The source timeframe, ROI schedule, 5x profit-ratio normalization, stop, trailing settings and indicator parameters are held fixed.  The only cross-variant changes are declared above.

## Evaluation contract

- Development: 2026-07-22 through 2026-07-28.
- Untouched comparison for up to two positive survivors: 2025-11-03 through 2025-11-09.
- Conditional continuous expansion: 2025-09-01 through 2025-09-30.
- Universe: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT.
- One account, one global position/entry slot, 3% planned loss per trade.
- Final 30-day pass requires after-cost daily geometric growth at least 1%, completed trades at least calendar days, positive expectancy, no global-slot violation or order rejection, and maximum drawdown no greater than 20%.

Development results may select a frozen interpretation but are not holdout evidence.  Any rule changed after viewing a period creates a new version and cannot reuse that period as untouched evidence.
