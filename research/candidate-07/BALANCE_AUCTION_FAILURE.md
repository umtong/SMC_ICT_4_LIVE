# Candidate-07 discarded hypothesis: OI-backed balance initiative

## Scope

This note records the independent balance-to-initiative candidate evaluated after the external-sweep/OI-release reversal was discarded. All orders, fills, fees, funding, positions, account equity and NAV were produced by NautilusTrader `BacktestEngine`; no separate backtest engine was introduced.

Frozen first BTC week:

```text
2025-12-22 through 2025-12-29
```

## Hypothesis

The candidate froze a completed rotational 12×5-minute balance before evaluating any later bar. A tradable initiative required:

```text
completed close outside the frozen balance
+ aligned taker aggressor flow
+ an unusually large increase in open interest
```

The next completed bars then had two possible causal branches:

1. **Accepted initiative** — price held outside the balance with same-direction redisplacement and no OI release.
2. **Failed initiative** — price returned inside the balance while opposite aggressor flow and release of the newly built breakout inventory appeared.

Each balance could be attempted once. Target and stop were fixed at `ENTRY_READY`; the inherited Nautilus execution layer rejected delayed entries whose remaining reward-to-risk had eroded and sized planned loss from current NAV at 3%, including fees, adverse ticks and funding reserve.

## Frozen Week-1 result

```text
balances locked           44
OI-backed initiative       9
accepted confirmations     4
failed-unwind entries       0
confirmation timeouts       5
trades                      4
wins / losses               0 / 4
net return                -10.8218%
daily geometric growth    -1.6239%
profit factor              0
maximum drawdown          10.8218%
active days                 4
weekly gate               FAIL
```

All four trades were accepted-initiative continuations and all four lost. This was a strategy-logic failure, not an execution or data-delivery failure: `smc4 doctor`, compilation, all state-machine tests, checksum data loading, CustomData alignment, Nautilus order submission and report generation completed successfully.

## Required single-variable ablation

The only changed variable was `use_open_interest=false`. Balance construction, price/flow conditions, targets, stops, order type, fees, slippage, funding, 3% current-NAV sizing and evaluation interval remained fixed.

```text
trades                     13
wins / losses               2 / 11
net return                -20.9627%
daily geometric growth    -3.3195%
profit factor              0.2252
maximum drawdown          20.9627%
active days                 7
weekly gate               FAIL
```

Branch contribution in the ablation:

```text
accepted initiative: 7 trades, 0 wins, approximately -19.0k USDT
failed initiative:   6 trades, 2 wins, approximately  -1.9k USDT
```

Removing OI increased frequency and worsened performance. OI build therefore filtered some low-quality breaks, but it did not create positive expectancy.

## Failure cause

The largest performance driver was the assumption that an outside close followed by a short outside hold represented persistent price discovery. The test results reject that assumption in this implementation:

- new OI can be opened by both sides and does not prove directional inventory dominance;
- a position can be opened outside a balance while passive liquidity is already absorbing it;
- an outside close does not measure whether the perpetual is rich or cheap relative to a contemporaneous valuation anchor;
- accepted-price classification did not require the valuation dislocation itself to persist or contract;
- the failed-initiative branch was too rare to offset continuation losses.

The loss was not repaired by removing OI, and changing balance width, touch count, OI rank or confirmation thresholds would only tune the same rejected causal assumption.

## Components that worked and are retained

The following components remain useful infrastructure or scenario primitives, but they are not evidence that this candidate is profitable:

- completed-data timestamp normalization and one-nanosecond causal bar ordering;
- checksum-verified Binance USD-M metrics and aggressor-flow loading;
- exact quarantine of invalid OI snapshots without interpolation or forward fill;
- neutral treatment of the first non-contiguous OI change after a gap;
- one-attempt lifecycle per frozen market structure;
- explicit `BALANCE_LOCKED → INITIATIVE_BREAK → CONFIRMED/INVALIDATED` transitions;
- fixed signal-time target and stop with delayed-entry RR erosion rejection;
- current-NAV 3% planned-loss sizing after fees, adverse ticks and funding reserve;
- branch-level events, Nautilus trade reports and NAV diagnostics.

## Disposition

The rule `rotational balance + outside close + OI build → continuation` is discarded and must not be restored by parameter tuning.

The next independent hypothesis measures the traded perpetual against the exchange-reported derivatives valuation anchor:

```text
valuation anchor = sum_open_interest_value / sum_open_interest
```

Direction will come from the sign of the actual price–valuation deviation, not from OI sign. A trade will be allowed only after:

```text
tail valuation dislocation
→ measurable contraction of that dislocation
→ opposite aggressor flow and price reversal
```

OI will classify whether the dislocation was accompanied by inventory build or release, but it will no longer determine trade direction.
