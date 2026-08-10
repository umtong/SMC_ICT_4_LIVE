# Candidate 57 — public ZaratustraV5 futures source v1 freeze

## External discovery signal

The public `ZaratustraV5.py` strategy is a five-minute long/short futures policy using aligned five-, fifteen- and thirty-minute states:

- long: RSI above 50 on all three horizons, PLUS_DI above 25 on all three, and close above the Bollinger middle band on all three;
- short: RSI below 50, MINUS_DI above 25 and close below the Bollinger middle band on all three;
- source leverage 10x;
- source stoploss -29.6% on leveraged profit, equivalent to approximately 2.96% underlying movement;
- source trailing activation at +7.1% leveraged profit and distance 1.3%, equivalent to approximately +0.71% activation and 0.13% trailing distance in the underlying;
- no ROI table and no source exit signal;
- entry is a completed-candle level, not only a rising edge.

A public uniform backtest reports 175,744 trades over 2021-01-01 through 2026-01-01 across 33 futures pairs, 77.9% wins, profit factor 1.34, 35,969% total profit, positive results in 60 of 61 months and every rolling three-month interval, with 43.83% account drawdown. Those figures used a broad universe, fixed stake and up to ten simultaneous trades. The public report also did not use one-minute detail for the five-minute trailing stop. The figures are a high-information discovery signal, not project evidence.

## Source-preserving project implementation

- All decisions use completed one-minute data causally aggregated into complete five-, fifteen- and thirty-minute candles.
- Fifteen- and thirty-minute indicators are forward-held only after their source candle closes.
- RSI uses Wilder period 14.
- PLUS_DI and MINUS_DI use Wilder period 14.
- Bollinger middle is the 20-period SMA of typical price, as in the public source.
- Chikou or any future-shifted field is absent.
- The exact level semantics and symmetric long/short logic are preserved in source cells.
- Cross-symbol collisions are arbitrated causally; only one candidate becomes an order.
- Trailing activation or ratchet created by a completed one-minute bar is usable only from the next completed minute. This removes same-five-minute-bar optimism from the public report.
- Current-NAV 3% planned-loss sizing, fees, adverse slippage, funding reserve and the global one-slot account are unchanged.

## Frozen structural cells

1. `source_level_both` — exact source entry, source 2.96% underlying stop.
2. `source_level_long` — exact source entry, long only.
3. `source_level_short` — exact source entry, short only.
4. `source_edge_both` — exact source state but one entry per false→true transition.
5. `structural_level_both` — exact source entry with invalidation beyond the nearest causal five-minute swing / aligned Bollinger-middle support or resistance plus ATR buffer.
6. `structural_level_short` — same structural risk with shorts only.

This is not a threshold grid. RSI 50, DI 25, periods, horizons and source trailing values do not vary.

## Evaluation allocation

All evaluation dates are after the end of the external report.

- Development: 2026-03-01 through 2026-03-14.
- Reserved comparison: 2026-05-01 through 2026-05-07.
- Conditional continuous account: 2026-06-01 through 2026-06-30.

Every case persists every completed trade, exit family, R distribution, symbol/side results and winner-versus-loser entry-state contrasts. Up to two development cells consume the reserved comparison: the strongest quality cell and, when different, the strongest mechanically valid opportunity-density cell. This is resource allocation, not a binary truth claim. The 30-day account is consumed only for a positive reserved survivor.

The strict final pass remains one continuous four-symbol account with after-cost geometric daily growth at least 1%, completed independent trades at least calendar days, positive expectancy, profit factor above one or no losses, maximum drawdown at most 20%, no liquidation and valid one-slot mechanics.
