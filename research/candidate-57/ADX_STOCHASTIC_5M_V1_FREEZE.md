# Candidate 57 — public ADXStochastic five-minute source v1 freeze

## External discovery signal

The public `ADXStochastic.py` strategy reports a Bybit-futures backtest from 2023-10-10 through 2024-04-10 using five-minute candles with one-minute detail, twenty pairs and up to five simultaneous trades:

- 1,767 trades, 9.66/day;
- 65.2% wins and profit factor 1.38;
- 4,959.85% account return;
- average holding time 77 minutes;
- 26.15% account drawdown;
- BTC and ETH strongly positive, XRP positive, SOL slightly negative.

The public code is long-only and uses:

- ADX(14) above 50;
- previous fast stochastic K and D below 20;
- current fast K crossing above current fast D;
- source leverage 9x;
- source ROI 4% immediately, 2% after 30 minutes and 1% after 60 minutes, equivalent to approximately 0.444%, 0.222% and 0.111% underlying price moves;
- source stoploss -10% leveraged, equivalent to approximately 1.111% underlying;
- source exit when ADX is below 25 and fast K is above 75. The public code repeats fast K twice; the likely intended second clause is fast D above 75.

The broad universe, fixed stake and five simultaneous trades differ from this project. The report is a high-information discovery signal, not project evidence.

## Source-preserving implementation

- Completed one-minute data are causally aggregated into complete five-minute candles.
- Fast stochastic uses K period 5 and D SMA period 3.
- ADX uses Wilder period 14.
- Entry is exactly the completed-candle crossover with prior K and D below 20.
- Source leverage, stop and ROI are normalized to underlying price before the project risk calculation.
- Current-NAV 3% planned-loss sizing, realistic costs and one global slot remain unchanged.
- Maximum holding time is 480 minutes.
- No same-source-candle future information is used.

## Frozen structural cells

1. `literal_source` — public duplicated-fast-K exit and source stop.
2. `corrected_exit` — replaces the duplicated second fast-K exit clause with fast D above 75.
3. `roi_stop_only` — removes the historically loss-heavy source exit; ROI, stop and maximum hold remain.
4. `structural_literal` — literal exit with stop beyond the recent five-minute swing plus ATR buffer.
5. `structural_corrected` — corrected exit with structural stop.

This is not a parameter grid. ADX 50/25, stochastic 20/75, indicator periods and ROI schedule do not vary.

## Evaluation allocation

All dates are after the external report.

- Development: 2026-01-15 through 2026-01-28.
- Reserved comparison: 2025-08-18 through 2025-08-24.
- Conditional continuous account: 2025-10-01 through 2025-10-30.

Every case persists every completed trade, exit family, R distribution, symbol results and winner-versus-loser entry-state contrasts. Up to two development cells consume the reserved comparison: the strongest quality cell and a different opportunity-density or structural-diagnostic cell when informative. This is resource allocation, not a binary truth claim. The 30-day account is consumed only for a positive reserved survivor.

The strict final pass remains one continuous four-symbol account with after-cost geometric daily growth at least 1%, completed trades at least calendar days, positive expectancy, profit factor above one or no losses, maximum drawdown at most 20%, no liquidation and valid one-slot mechanics.
