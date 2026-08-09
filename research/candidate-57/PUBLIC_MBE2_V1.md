# Candidate 57 public MBE2 reuse freeze

Candidate 57 reuses the effective Freqtrade interface-v3 policy in `remiotore/ccxt-freqtrade/strategies/myshortingstrategiembe2.py` at blob `d312e07abc99ffd5631a992fc67a4e97a8768c0a`.

The source's effective explicit entry surface is tested without treating its legacy `buy` and `sell` columns as interface-v3 entries or exits:

- 5-minute complete candles and 140-candle causal startup;
- long when RSI(14) crosses above 30, TEMA(9) is at or below the 20-period middle band, and TEMA is rising;
- short when RSI(14) crosses below 70, TEMA is above the middle band, and TEMA is falling;
- source ROI schedule `0:7.9%, 15:4.7%, 41:3.2%, 114:11%, 180:0.7%, 420:0.1%`;
- source trailing activation `2.5%`, trailing distance `1.5%`, and stoploss `22%` in source profit space.

The public result reports average effective leverage near 6.46x. The tournament therefore tests both-side, long-only, and short-only versions at 6.46x plus a both-side 10x source-cap sensitivity. Profit-space stop, ROI, and trailing values are translated into underlying price fractions before the project 3% current-NAV risk sizing is applied.

Evaluation is adaptive and predeclared:

1. development: 2026-07-22 through 2026-07-28;
2. untouched confirmation: 2025-02-10 through 2025-02-16;
3. 30-day continuous account: 2024-10-01 through 2024-10-30;
4. 180-day continuous account: 2024-03-01 through 2024-08-27.

Each replay loads two unscored warmup days. All entries are causal RSI-cross episodes, and only completed positions count toward frequency. The reused NautilusTrader stack enforces the four-symbol single global slot, realistic costs, continuous NAV, and exact 3% planned-loss contract. Process and account-contract failures are classified separately from mechanically valid strategy-logic failures.
