# Candidate 57 public-el reuse freeze

Candidate 57 directly reuses the public `remiotore/ccxt-freqtrade`
`strategies/el.py` entry and exit policy at blob
`d8c7d6a76386e47747d9c4cbffafe189313042bb`.

The source-fixed surface is:

- 5-minute complete candles, 2,000-candle causal startup;
- long when `close < EMA(12)*0.915` and either
  `EWO(SMA50,SMA200)>4.428 && RSI(14)<44` or `EWO<-12.383`;
- short when `close > EMA(72)*1.008`;
- opposite source condition exits;
- source ROI schedule `0:21.9%, 24:8.7%, 67:2.4%, 164:0%`,
  source trailing `3%` activation and `0.5%` distance, and source stoploss
  `24.2%`, all normalized by the source's effective 10x leverage before
  applying the project's 3% current-NAV planned-loss sizing.

The workflow tests rising-edge both-side, long-only, and short-only variants.
A source level-reentry replay is diagnostic only and can never satisfy the
project's independent-trade-frequency gate.

Evaluation is adaptive and predeclared:

1. development: 2026-07-22 through 2026-07-28;
2. untouched confirmation: 2025-02-10 through 2025-02-16;
3. 30-day continuous account: 2024-10-01 through 2024-10-30;
4. 180-day continuous account: 2024-03-01 through 2024-08-27.

Every replay loads eight unscored warmup days. All entries use the reused
NautilusTrader execution/accounting stack, realistic costs, one global slot
across BTCUSDT/ETHUSDT/SOLUSDT/XRPUSDT, and the project 3% risk budget.
The implementation separates process/account-contract failures from validly
executed strategy-logic failures before any promotion.
