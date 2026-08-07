# Candidate 13 — Dynamic Price-Discovery Auction Transfer

Candidate 13 is a frozen, direct-execution distillation of the strongest causal
mechanism found elsewhere in the repository. It does **not** copy historical
results or the patch/materialization chain. It copies only the latest
materialized state machine and evaluates it unchanged on newly predeclared
intervals.

## Market hypothesis

BTC, ETH, SOL and XRP are treated as four sensors of one liquid crypto risk
auction, not as four independently optimized strategies.

1. The current price-discovery leader is selected from trailing **completed**
   24-hour quote notional. BTC is not hard-coded.
2. A leader may trade a failed-auction reversal (FAR) or a causally accepted
   continuation (AAC).
3. A follower may trade FAR only when all three completed peer paths support the
   reversal and its own displacement, timing and adverse-auction checks pass.
4. Missing or asynchronous peer state fails closed.
5. A single global mutex allows at most one pending entry or open position over
   all four instruments.
6. Position size is derived from current total NAV and the exact 3% planned-loss
   budget. NautilusTrader owns fills, fees, margin, positions and NAV.

This keeps pattern detection separate from the full trading scenario:
liquidity pool -> sweep/acceptance event -> completed peer evidence -> local
structure confirmation -> entry -> structural invalidation/target.

## Why this was selected

The motivating implementation produced 12 wins and no losses over candidate-11
W4-W8 (35 observed days), while W9 produced no trades. Those intervals are
source-selection evidence only and are **not** candidate-13 validation.

Candidate 13 tests the locked source on five intervals selected before their
market files are downloaded. Exact dates, source Git blob IDs and aggregate
gates are in `protocol.json`.

## Frozen holdouts

| Week | Interval `[start, end)` | Role |
|---|---|---|
| W10 | 2025-04-14 to 2025-04-21 | primary |
| W11 | 2023-03-20 to 2023-03-27 | primary |
| W12 | 2023-06-20 to 2023-06-27 | primary |
| W13 | 2024-09-17 to 2024-09-24 | stress |
| W14 | 2024-12-31 to 2025-01-07 | stress |

Selection seed: `2026080813`.

## Aggregate success gate

All five intervals are evaluated with the same code and parameters. Candidate
success requires all of the following:

- after-cost NAV daily geometric growth >= 1% over 35 observed calendar days;
- at least 10 closed trades and at least four active weeks;
- win rate >= 80%, payoff ratio >= 1.2 when losses exist;
- maximum recorded closed-trade drawdown <= 20%;
- no single week contributes more than 60% of positive log growth;
- every evidence, exact-risk, global-slot, partial-fill and liquidation audit
  passes.

Individual weeks never set `success_claim=true`. Only `aggregate.py` may do so,
using the gate already frozen in `protocol.json`.

## Reproduction

The GitHub workflow uses the repository's pinned common image and starts with
`smc4 doctor`. No package installation and no separate backtest engine are
used.

```bash
bash research/candidate-13/run_week.sh W10
python research/candidate-13/aggregate.py \
  --results research/candidate-13/results \
  --protocol research/candidate-13/protocol.json \
  --output research/candidate-13/aggregate.json
```
