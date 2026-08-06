# Candidate 05 — Liquidity Response Transition (LRT)

Candidate 05 is an independent BTC-first SMC/ICT day-trading system. Its core
question is not whether a candle crossed a visible high or low, but whether the
aggressive order flow that crossed the liquidity pool continued to move price or
was absorbed by replenishing liquidity.

```text
past-only liquidity pool
  -> causal pool violation
  -> aggressive-flow / price-response / depth-response classification
       -> rejection: reclaim + opposite displacement
       -> acceptance: hold outside + first retest
  -> NautilusTrader bracket entry
  -> opposing liquidity or causal expansion target
  -> structural stop / funding-safe day-trade exit
```

No standalone simulator or candidate-specific matching/accounting engine exists
in this directory. Every weekly and long evaluation uses NautilusTrader's
`BacktestNode`, `ParquetDataCatalog`, matching engine, contingent orders, fee
model, margin account, liquidation path, positions, portfolio, and NAV.

## Structural hypothesis

A liquidity sweep has two economically different continuations:

1. **Rejection / failed auction** — aggressive buying above a high (or selling
   below a low) is large, but price response is inefficient, the consumed side
   of visible depth replenishes, and price reclaims the pool. A later opposite
   displacement is required before entry.
2. **Acceptance / repricing** — aggressive flow remains directionally aligned,
   price response is efficient, threatened depth withdraws, and price holds
   outside the consumed pool. Entry is allowed only on the first causal retest;
   the breakout bar is never chased.

The implementation distinguishes the *pool detector* from the *trade scenario*.
Confirmed swings and completed four-hour ranges merely arm liquidity locations.
A trade exists only after the subsequent response state is classified and
confirmed.

## Causal data contract

The execution stream is Binance USD-M `BTCUSDT` one-minute klines. Additional
observations are built from the same exchange's public daily archives:

- `aggTrades`: aggressive buy/sell notional, 15-second and 60-second flow,
  activity burst, travelled path, and price-response efficiency;
- `bookDepth`: time-based last-known notional at the ±1% and ±2% bands, depth
  imbalance, replenishment/withdrawal, and snapshot age;
- `klines`: completed OHLCV bars used by NautilusTrader for execution replay.

Each archive and `.CHECKSUM` file is verified. A feature row is labelled with
the corresponding completed kline close timestamp and the strategy rejects any
row whose `observed_time_ns` is later than the current Nautilus event or whose
depth snapshot is stale. Confirmed swing pivots preserve both their historical
pivot time and the later time when the right-hand bars made them knowable.

## Explicit SMC/ICT definitions

| Concept | Candidate 05 machine definition |
|---|---|
| Liquidity pool | A causally confirmed swing extreme or completed four-hour range extreme; near-equal levels merge and increase strength. |
| Sweep | Price penetrates an eligible, past-known pool by an ATR-normalized minimum after approaching from the correct side. |
| Rejection / CHoCH candidate | Sweep-direction aggressive flow is present, impact efficiency is low, consumed-side depth replenishes, and price reclaims the pool. |
| Acceptance / BOS candidate | Price closes and holds outside, aggressive flow is aligned, impact efficiency is high, and threatened depth withdraws. |
| Displacement | A structure-breaking directional body with aligned flow, high close location, and non-trivial response efficiency. |
| First retest | The first post-acceptance touch of the consumed level that closes outside while counterflow is exhausted. |
| Invalidation | Rejection extreme accepted, accepted price returns inside, structural stop fills, or setup expires. |
| Target | Nearest still-active opposing pool with sufficient cost-after R; otherwise a branch-specific causal expansion target. |

## Risk and execution

Quantity always uses current full-account NAV and the fixed project risk budget:

```text
risk budget = current NAV * 3%
quantity = risk budget / expected loss per unit
```

Expected loss per unit includes adverse entry/stop slippage, entry and stop
all-in costs, and the structural stop distance. Quantity is rounded only to the
instrument increment. There is no model-score risk multiplier, candidate
notional cap, or discretionary leverage cap. The venue model exposes the
exchange leverage ceiling and keeps Nautilus liquidation enabled.

The strategy permits at most one pending entry intent or one open position.
Positions are flattened before the next 00:00, 08:00, or 16:00 UTC funding
boundary and after a finite intraday holding window.

## Frozen staged evaluation

The three starts were drawn before any market result with `random.Random(5005)`
from all seven-day starts between 2023-01-01 and 2025-12-25. Starts must be at
least 28 days apart. `select_weeks.py` reproduces the draw.

| Stage | UTC evaluation dates |
|---|---|
| week-1 | 2023-07-09 through 2023-07-15 |
| week-2 | 2024-01-15 through 2024-01-21 |
| week-3 | 2023-09-08 through 2023-09-14 |
| long, only after all weeks pass | 2024-03-01 through 2024-05-30 (91 days) |

Each stage uses two prior warm-up days. The pipeline stops immediately at the
first failed week. The frozen weekly gate requires all of the following after
costs: geometric daily NAV growth at least 1%, at least seven trades and four
wins, at least 40% win rate, activity on at least four days, drawdown no greater
than 20%, no winner contributing more than 55% of gross profit, positive NAV,
no liquidation or rejected order, and the one-entry/position invariant.

These are promotion gates, not a loss function for parameter fitting.

## Reproduction

Run in the prebuilt project Codespace / Dev Container; do not install another
engine:

```bash
smc4 doctor
export PYTHONPATH=research/candidate-05
python -m unittest discover \
  -s research/candidate-05/tests \
  -p 'test_*.py' -v

python research/candidate-05/candidate.py pipeline \
  --config research/candidate-05/config.json \
  --weeks research/candidate-05/weeks.json \
  --cache .cache/candidate-05 \
  --output artifacts/candidate-05 \
  --max-weeks 1
```

Only after week-1 passes, increase `--max-weeks` sequentially. Long evaluation
is enabled with `--run-long` and is internally blocked unless all three weeks
passed.

Each completed stage writes:

- `metrics.json`, `pipeline_summary.json` — cost-after NAV and promotion gates;
- `orders.csv`, `positions.csv`, `account.csv`, `equity.csv` — Nautilus-owned
  execution/account evidence;
- `scenario_events.jsonl` — causally validated state transitions;
- `strategy_diagnostics.json`, `closed_scenarios.json` — scenario failure and
  attribution data;
- `data_manifest.json`, `raw_evidence.json`, `run.json` — checksums and runtime
  provenance.

## Controlled diagnosis order

When a week fails, the diagnosis order is fixed:

1. execution or state-chain defect;
2. insufficient detected pool/sweep opportunities;
3. rejection-versus-acceptance classification quality;
4. confirmation timing;
5. stop/target geometry and cost-after expectancy.

Only one causal family is changed at a time. A branch with weak or negative
cost-after expectancy and no monotonic relation between its response evidence
and outcome is removed rather than protected with additional filters.

## Known failure conditions

- Public `bookDepth` is aggregated at percentage bands and sampled roughly every
  30 seconds; it is a replenishment/withdrawal observation, not exact queue
  position. Stale snapshots invalidate classification.
- One-minute OHLC execution cannot reconstruct tick ordering exactly.
  NautilusTrader's adaptive bar high/low ordering, latency, all-in costs, and
  contingent orders are therefore mandatory and performance must survive this
  uncertainty.
- A two-sided bar that crosses both high and low pools is treated as an
  unresolved volatility shock, not two independent opportunities.
- A pool can be consumed without yielding a trade. Reusing the same liquidity
  event until a favorable entry appears is prohibited.
- The initial BTC screen is an experiment in scenario logic, not BTC-specific
  optimization. ETH/SOL/XRP transfer is prohibited until BTC survives the
  staged gates unchanged.

## Research basis

- Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*: short-horizon
  price changes are more robustly related to order-flow imbalance and depth than
  to trade volume alone.
- Taranto, Bormetti & Lillo, *The adaptive nature of liquidity taking in limit
  order books*: persistent predictable order flow does not imply equal price
  impact; liquidity response is asymmetric.
- NautilusTrader high-level backtesting documentation: `BacktestNode` and the
  Parquet catalog are the production-oriented path whose strategy components
  carry forward to live trading.
- Binance public-data repository: daily futures klines and aggregate trades are
  exchange-derived archives with SHA-256 checksum files.

## Current status

The independent state machine, causal feature builder, Nautilus-only runner,
risk algebra, deterministic week draw, unit tests, and first-week workflow are
implemented. The authoritative performance status is the committed workflow
artifact and `metrics.json`; this section must not be promoted from an ad-hoc
calculation.
