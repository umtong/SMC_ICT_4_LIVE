# Candidate-09 v0.1 — LRAE frozen-week decision

## Decision

**Status: `LOGIC_GATE_FAILED`; v0.1 is discarded as a deployable candidate.**

The run completed in NautilusTrader 1.230.0 with no rejected orders and a flat ending state. The failure is therefore a strategy-logic failure, not an incomplete run. The base candidate and its single-variable `no_flow` ablation were evaluated on the same three deterministic BTCUSDT weeks, the same 6.5 bps/side effective cost, and the same 3% full-NAV risk sizing.

## Frozen selection

- Seed: `20260806`
- Population: every Monday from `2023-01-02` through `2025-12-22`
- Algorithm: `random.Random(seed).sample(population, 3)`
- `w1-discovery`: `2024-10-14` through `2024-10-20` UTC
- `w2-replication`: `2024-05-13` through `2024-05-19` UTC
- `w3-replication`: `2025-01-13` through `2025-01-19` UTC

## Base result

| Week | Final NAV | Geometric/day | MDD | Trades | Win rate | Profit factor |
|---|---:|---:|---:|---:|---:|---:|
| w1-discovery | 35,540.10 | -13.7385% | 64.46% | 63 | 20.63% | 0.3586 |
| w2-replication | 29,690.25 | -15.9265% | 70.31% | 58 | 13.79% | 0.2116 |
| w3-replication | 45,838.73 | -10.5450% | 59.27% | 100 | 27.00% | 0.6552 |

Pooled base result:

- Cost-after geometric daily NAV growth: **-13.4314%**
- Closed trades: **221**
- Win rate: **21.72%**
- Profit factor: **0.4551**
- Gross positive trade PnL: **157,809.42 USDT**
- Gross negative trade PnL: **346,740.33 USDT**

Scenario attribution:

- `absorption_reclaim_reversal`: 208 trades, 45 wins, -176,874.07 USDT
- `depletion_acceptance_continuation`: 13 trades, 3 wins, -12,056.84 USDT

## One-variable ablation

`no_flow` removes only the aggressor-flow polarity gate. It produced:

- Cost-after geometric daily NAV growth: **-18.4106%**
- Closed trades: **295**
- Win rate: **21.02%**
- Profit factor: **0.4418**

Removing flow worsened pooled daily growth by 4.9792 percentage points and admitted 74 additional mostly losing trades. The aggressor-flow sign therefore supplied weak but real selectivity. It did not repair the underlying level, branch, or execution geometry.

## Controlled implementation checks

Three implementation defects were isolated and repaired without changing weeks, data, parameters, fees, risk, or scenario logic:

1. GitHub container user lacked runner temporary-directory permission; only the workflow user was changed.
2. NautilusTrader 1.230.0 exposes `PositionClosed.duration_ns`, not `duration`; only the adapter field was changed.
3. Multiple engines in one process attempted repeated global Rust logger initialization; only logging was changed to `LoggingConfig(bypass_logging=True)`.

After these controlled fixes, `smc4 doctor`, compilation, five causal/risk tests, six NautilusTrader runs, report generation, and artifact upload all passed.

## Failure cause

The dominant error was treating repeatedly refreshed 20-bar highs/lows as durable liquidity pools. Across 21 days, this generated 3,162 nominal breaches, 828 reclaims, 1,976 acceptances, 701 held retests, and 221 entries. These levels were ordinary local extrema, not sparse first-use pools backed by a stable market mechanism.

The second dominant error was stop and cost geometry. Median stop distance was approximately 0.205 ATR, while the fixed conservative effective cost consumed roughly 64% of planned per-unit loss on average. This forced very large notional sizes. The engine still respected the 3% NAV loss budget, but small adverse price movement plus costs repeatedly consumed the whole budget.

Forensic decomposition across the base runs found approximately:

- Price movement before commissions: **+93,453.13 USDT**
- Effective commissions/cost allowance: **-282,384.04 USDT**
- Net: **-188,930.91 USDT**

This does not establish usable alpha: it establishes that gross movement capture was too small and too costly for the scenario frequency and stop geometry.

## Working components retained as research evidence

- Aggressor-flow polarity improved selectivity relative to its ablation.
- Completed-bar causality and delayed pivot observation worked as specified.
- Cost-aware quantity calculation kept planned stop loss at or below 3% of current NAV.
- One-position/order-list enforcement, final flattening, and NautilusTrader accounting completed without rejections.
- Ambiguous one-minute bars that breached both range sides were not traded.

## Known failure conditions

- A rolling local high/low is reused as if it represented a durable external liquidity pool.
- A reclaim bar is treated as sufficient absorption evidence without trade-sequence or impact evidence.
- Price invalidation is close enough that round-trip cost becomes a major fraction of the 3% loss budget.
- A bar breaches both sides and intrabar order is unknown.
- No previously observed external target exists beyond continuation acceptance.
- Reclaim, acceptance, or retest confirmation does not occur before its causal timeout.
- Bar-only data cannot reconstruct trade sequence, queue position, spread path, or true order-book replenishment.

## Reproduction and retained artifact

```bash
smc4 doctor
python -m unittest discover -s research/candidate-09/tests -p 'test_*.py' -v
PYTHONPATH=src python research/candidate-09/run_research.py \
  --config research/candidate-09/config.json \
  --output artifacts/candidate-09
```

- Validated source commit: `ea34d43f027b391bc355d3352707383ce6e98787`
- Workflow run: `31087095706`
- Artifact ID: `8961867682`
- Artifact name: `candidate-09-evidence-ea34d43f027b391bc355d3352707383ce6e98787`
- Artifact ZIP SHA-256: `951a58254310dec2908b847ca9f1f2409770fcc284e0ac15188efcb0ff901d00`

The artifact contains run manifests, source/data hashes, normalized weekly data, events, plans, trades, fills, positions, account reports, equity curves, base/no-flow metrics, aggregate decision, and summary.