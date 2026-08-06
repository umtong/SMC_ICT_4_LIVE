# Candidate 08 — Liquidity Sweep Bifurcation

Candidate 08 is an independent, complete SMC/ICT day-trading candidate. It does not assume that a
liquidity sweep must reverse. It models a pool interaction as a causal bifurcation:

- **rejection** — the swept level is reclaimed, then opposite displacement confirms failure to
  accept price beyond the pool;
- **acceptance** — displacement closes beyond the pool, then a retest holds the level and confirms
  continuation toward the next external liquidity pool.

A detected swing or FVG-like displacement is not itself a trade. `logic.py` is the pattern detector
and scenario state machine; `strategy.py` is only the NautilusTrader order/risk adapter. The real
NautilusTrader engine owns replay, contingent orders, fills, fees, margin, liquidation, portfolio,
and reports.

## Fixed research protocol

The screen dates were chosen before outcomes were read with seed `8008`: Monday starts were
uniformly shuffled from 2024-01-01 through 2025-12-22 and accepted only when at least 84 days apart.

| stage | UTC interval |
|---|---|
| screen-01 | 2024-04-08 to 2024-04-15 |
| screen-02 | 2025-06-09 to 2025-06-16 |
| screen-03 | 2025-09-29 to 2025-10-06 |
| long evaluation, only after screen promotion | 2024-01-01 to 2026-07-01, exclusive end |

BTC is tested first. ETH, SOL, and XRP transfer tests are intentionally deferred until BTC survives
the fixed screen and long evaluation without symbol-specific rule changes.

## Immediate reproduction

Use the prebuilt project environment. Do not install or replace NautilusTrader.

```bash
smc4 doctor
python research/candidate-08/test_logic.py -v
python research/candidate-08/run.py \
  --suite screen \
  --output artifacts/candidate-08-screen
```

The runner downloads official Binance Vision USD-M `BTCUSDT` one-minute kline archives, verifies
every published SHA-256 checksum, assigns each bar its source `close_time` as the first observable
time, and then passes the bars to NautilusTrader.

Output follows the project contract and includes `run.json`, `metrics.json`,
`scenario_events.jsonl`, orders, fills, positions, account history, data hashes, trade intents, and
scenario-attributed position outcomes.

## Risk and execution contract

- current total USDT account NAV is the sizing base;
- planned loss is capped at exactly 3% of current NAV;
- per-unit expected loss includes structural stop distance, entry and stop fees, and two adverse
  ticks;
- no signal-score multiplier, nominal cap, or candidate-specific leverage cap is added;
- one market entry plus OUO stop-market/limit-target bracket is submitted through NautilusTrader;
- at most one position or new-entry order exists;
- every fill is charged an effective 6 bp fee and the fill model applies one-tick adverse slippage
  with probability 1;
- positions are not opened within 185 minutes of a UTC funding boundary and time out after 180
  one-minute bars, so the baseline does not depend on unmodeled funding receipts/payments;
- liquidation is enabled in the Nautilus venue model.

The 6 bp charge is deliberately all-in: 5 bp base taker cost plus 1 bp per-fill reserve for execution
and residual funding uncertainty. A zero sub-minute latency value is used because a nonzero latency
cannot be represented faithfully by one-minute OHLC events and would become an artificial complete
bar delay. This limitation and the required tick/order-book upgrade are recorded in
`KNOWN_FAILURES.md`.

## Files

- `logic.py` — causal pools, sweep classification, state transitions, structural stop/target
- `strategy.py` — NAV risk sizing and Nautilus OUO bracket submission
- `data.py` — official checksum-verified Binance Vision data loader
- `run.py` — reproducible Nautilus engine runner and diagnostics
- `config.json` — fixed scenario definitions, dates, costs, and promotion gate
- `test_logic.py` — future-information, bifurcation, timing, and risk-contract tests
- `STRATEGY_SPEC.md` — complete mechanical definitions
- `REPRODUCIBILITY.md` — environment/data/output contract
- `RESULTS.md` — executed evidence and promotion decision
- `KNOWN_FAILURES.md` — structural and data/execution failure conditions
