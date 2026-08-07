# Candidate-09 v14 three-year failure diagnosis

## Evidence identity

- Workflow run: `31142369549`
- Trigger commit: `98db58069fe36be029aeacc3ef34cc136fd2dbcd`
- Artifact: `candidate-09-v14-fixed-31142369549`
- Artifact ID: `8980300421`
- Artifact SHA-256: `ebbab1e2b01556002b8299e38ca56061cc03efcc90fe7a99f965bd6112b35632`
- Frozen BTC interval: `2022-01-01T00:00:00Z` through `2025-01-01T00:00:00Z` exclusive
- Doctor, compile, and all 13 contract tests passed.
- NautilusTrader remained the execution and accounting engine.
- Signal, stop, target, cost, risk fraction, fixed weeks, and long interval were unchanged.

## Exact long result

| Metric | Result |
|---|---:|
| Starting modeled NAV | 100,000.00 USDT |
| Ending modeled cost-after NAV | 6.383359 USDT |
| Total return | -99.9936166% |
| Daily geometric return | -0.8774445% |
| Maximum drawdown | 99.9936166% |
| Trades | 697 |
| Wins / losses | 136 / 561 |
| Win rate | 19.5122% |
| Profit factor | 0.506476 |
| Mean realized R | -0.463242 |
| Maximum consecutive losses | 19 |
| Active months | 36 / 36 |
| Sizing-infeasible signals after severe damage | 44 |
| Rejected orders | 0 |
| Implementation status | OK |

## Cost decomposition

| Component | USDT |
|---|---:|
| Gross price PnL before all modeled transaction costs | 24,508.115900 |
| Native commissions | 30,184.683344 |
| Extra reserve to reach the frozen composite fill cost | 94,317.049196 |
| Total modeled costs | 124,501.732541 |
| Cost-after PnL | -99,993.616641 |

Gross price movement was slightly positive, but it was only 19.7% of total modeled costs. This is not a fee-only implementation bug: the trade-selection process generated too many low-quality reversals for its turnover.

## Exit-path diagnosis

| Exit path | Trades | Wins | Cost-after PnL |
|---|---:|---:|---:|
| Protective stop | 554 | 0 | -200,706.35 |
| Equilibrium target | 106 | 106 | +92,327.59 |
| Time/other exit | 37 | 30 | +8,385.14 |

- 1–3 minute holdings: 243 trades, only 2 wins.
- 1–2 minute holdings: 172 trades, 170 losses.
- The fixed-week success had concealed this high-frequency false-failure path.

## State path

| previous_state   |   trades |   wins |   win_rate |   gross_price_pnl |   modeled_net_pnl |    mean_r |   median_duration_min |
|:-----------------|---------:|-------:|-----------:|------------------:|------------------:|----------:|----------------------:|
| ACCEPTED         |      519 |     97 |   0.186898 |           21841.7 |          -75094.7 | -0.487589 |                   6   |
| RETESTED         |      178 |     39 |   0.219101 |            2666.4 |          -24898.9 | -0.392253 |                   8.5 |

## Source-auction horizon

|   horizon_minutes |   trades |   wins |   win_rate |   gross_price_pnl |   modeled_net_pnl |    mean_r |   median_duration_min |
|------------------:|---------:|-------:|-----------:|------------------:|------------------:|----------:|----------------------:|
|                15 |      183 |     31 |   0.169399 |           8762.7  |         -29531    | -0.544436 |                     5 |
|                60 |      439 |     86 |   0.1959   |          12154.2  |         -68027.8  | -0.466388 |                     6 |
|              1440 |       75 |     19 |   0.253333 |           3591.26 |          -2434.82 | -0.246717 |                    19 |

## Drawdown speed

| Drawdown threshold | First crossed | Modeled NAV | Closed trades |
|---:|---|---:|---:|
| 10.0% | 2022-01-18 08:47 UTC | 88,270.28 | 10 |
| 25.0% | 2022-01-21 23:03 UTC | 73,526.69 | 16 |
| 50.0% | 2022-02-02 15:03 UTC | 49,492.54 | 38 |
| 75.0% | 2022-05-18 19:21 UTC | 24,854.56 | 138 |
| 90.0% | 2022-07-12 14:45 UTC | 9,918.06 | 207 |
| 95.0% | 2022-09-01 18:21 UTC | 4,921.22 | 260 |
| 99.0% | 2023-03-06 17:12 UTC | 989.73 | 356 |
| 99.9% | 2024-02-20 18:14 UTC | 97.48 | 510 |

## Classification

**Logic failure, not implementation failure.**

The gate weeks happened to contain a favorable cluster of accepted-breakout failures, but the frozen three-year run shows that a single opposite-displacement close after outside acceptance is not sufficient evidence that the external auction has persistently failed. The current engine requires two outside closes to establish acceptance but only one inside close to reverse that state and enter immediately. The next controlled variable is therefore confirmation symmetry, not a return-tuned threshold.

## Frozen next test

`v17 persistent internal reacceptance`:

1. Exact v14 acceptance, flow, target, cost, and risk contracts remain unchanged.
2. The first opposite displacement close inside the failed boundary moves the scenario to `FAILURE_ACCEPTANCE_PENDING`; it does not enter.
3. The next completed bar must remain inside the same failure buffer.
4. If it does, enter at that second completed close with invalidation beyond the failed boundary and both confirmation bars.
5. If it does not, restore the prior `ACCEPTED` or `RETESTED` state without entry.
6. Exact causal controls:
   - `single-close`: exact v14;
   - `after-retest-only`: persistence only after a defended retest;
   - `direct-only`: persistence only for direct accepted-state failures.
