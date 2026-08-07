# Candidate-02 v103 verdict — endogenous turnover-clock order-flow regimes

## Decision

`v103` is rejected as a standalone trading system after its prospectively locked first BTC week and its one prospectively locked ablation.

```text
first evaluation week: 2025-11-17 00:00 UTC to 2025-11-24 00:00 UTC
engine: NautilusTrader 1.230.0
risk fraction: current account NAV × 3%
custom backtest engine: false
second week allowed: false
long evaluation allowed: false
final status: FIRST_WEEK_REJECT_AFTER_SINGLE_ABLATION
```

## Implementation errors separated from strategy logic

Two execution-plumbing errors occurred before any strategy result existed.

1. The prior router depended on a pull-request workflow which was absent from the default branch, so no v103 performance job was created.
2. The first branch-runner attempt stopped before data collection because the fixed root container rejected the runner-owned checkout as a Git `dubious ownership` directory.

Only the workflow was changed. The locked week, core blob, config blob, scenario rules, costs, risk fraction, and ablation were unchanged. The same week was then rerun successfully.

Successful immutable run:

```text
run_id: 31141672951
commit: ed1c1ec068d3b593eb7e810e834c6468d9ec74e4
full evidence artifact id: 8980052135
full evidence digest: sha256:4f9642104f5254b1750608fd143647b9bcf1d21c6799acd2c36e4a891dd14722
slim diagnostic artifact id: 8980144934
slim diagnostic digest: sha256:62d2c75cb17fc2764e1a3d4338e4b05293cd14aae890dbe78c4c8c8012435e83
```

## Locked first-week results

| Variant | Trades | Win rate | Cost-after PF | NAV factor | Geometric growth/day | MDD |
|---|---:|---:|---:|---:|---:|---:|
| turnover 6 | 19 | 26.32% | 0.399 | 0.7669 | -3.720% | -26.61% |
| turnover 8, central | 19 | 31.58% | 0.427 | 0.7886 | -3.336% | -25.65% |
| turnover 10 | 16 | 43.75% | 0.845 | 0.9571 | -0.625% | -17.94% |
| retained-only, turnover 8 | 19 | 31.58% | 0.427 | 0.7886 | -3.336% | -25.65% |
| absorbed-only, turnover 8 | 0 | — | — | 1.0000 | 0.000% | 0.00% |
| locked ablation: no depth-refill ceiling | 22 | 31.82% | 0.450 | 0.7659 | -3.738% | -30.07% |

The frequency problem was solved: all active variants produced 2.29–3.14 trades/day. The failure was therefore not insufficient opportunity.

## Cost decomposition

The wider turnover-10 clock retained a small gross directional effect but did not produce tradable after-cost alpha.

| Variant | Price PnL before commissions | Commissions | Net PnL |
|---|---:|---:|---:|
| turnover 6 | +239.33 USDT | 23,544.80 USDT | -23,305.47 USDT |
| turnover 8 | +290.55 USDT | 21,430.24 USDT | -21,139.69 USDT |
| turnover 10 | +11,378.39 USDT | 15,669.70 USDT | -4,291.31 USDT |
| no-depth-refill ablation | +1,978.41 USDT | 25,386.35 USDT | -23,407.95 USDT |

For turnover 10:

- winning trades had mean structural stop distance 0.397% and mean round-trip cost equal to 0.692% of entry NAV;
- losing trades had mean structural stop distance 0.300% and mean round-trip cost equal to 1.204% of entry NAV;
- all 16 exits were target or stop exits; no time exit explains the result;
- SELL trades were +791.80 USDT net while BUY trades were -5,083.10 USDT net, so the week also contains material directional asymmetry which cannot be generalized.

Risk sizing itself respected the 3% planned-loss budget. The economic failure came from scenario geometry: very small, non-liquidity-based stop distances produced large risk-sized quantities, and the fixed packet-multiple targets did not compensate for realistic entry/exit costs.

## Logical failure

The intended mutually exclusive regime classifier collapsed to one state.

- every central turnover-8 trade was `RETAINED_IMPACT_PRICE_DISCOVERY`;
- `ABSORBED_FLOW_EXHAUSTION` produced zero signals;
- removing the depth-refill ceiling added three trades and worsened NAV and drawdown.

The absorbed state required both midpoint reclamation and either weak spot confirmation or basis domination after already requiring aligned second-packet aggressive flow. In this week those conditions formed an effectively inactive conjunction. The retained state therefore acted as a standalone continuation pattern rather than a balanced response classifier.

The target and invalidation were also not causal liquidity objectives:

- continuation target = fixed multiple of the first packet move;
- continuation stop = second-packet extreme plus an ATR buffer;
- neither referenced an independently formed, pre-existing liquidity pool.

This violates the project requirement that a detector must be embedded in a market-state and liquidity scenario rather than traded for its own sake.

## What worked and is retained

The following parts remain useful, but not as a complete strategy.

1. Non-overlapping event-time packets produced enough independent opportunities without fixed quarter-hour anchoring.
2. The turnover-10 variant had positive price PnL before commissions, indicating that slower event-time persistence can be a useful contextual feature.
3. Spot confirmation, basis-share control, and depth-refill control reduced false continuation attempts; removing depth refill made results worse.
4. The causal timestamp contract and NautilusTrader execution/risk evidence passed.

These elements may be reused only as context or confirmation inside a full liquidity scenario. Their v103 thresholds are not promoted as optimized trading parameters.

## Rules carried forward

The next candidate must satisfy all of the following before its first unseen week is opened.

1. Separate liquidity/event detectors from the trading scenario state machine.
2. Begin from a pre-existing, causally known liquidity objective, not from a packet or candle pattern.
3. Classify a raid as rejection or acceptance before choosing reversal or continuation.
4. Require a structural market-state transition and an executable retracement; do not market-enter at the end of a displacement packet by default.
5. Place invalidation beyond the scenario-defining liquidity extreme, not at an arbitrary fraction of a recent move.
6. Target the nearest valid opposing/next liquidity pool formed before the decision.
7. Reject a trade when expected execution costs consume an economically dominant share of the structural move; this is a scenario viability condition, not a notional cap.
8. Preserve turnover-10/order-flow persistence only as one confirmation feature.
9. Prelock one new unseen BTC week and one ablation before collection.
10. Do not run a second week unless the first-week after-cost NAV gate is passed by the central rule and a neighboring structural definition.
