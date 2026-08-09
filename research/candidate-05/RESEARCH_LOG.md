# Candidate 05 research log

## Fixed research contract

All performance evaluations use NautilusTrader `BacktestNode` with the repository's
fixed image. Candidate code may transform public observations into causal features,
but it does not match orders, create fills, maintain positions, calculate fees,
simulate margin/liquidation, or construct NAV outside NautilusTrader.

The BTC week draw was frozen before results with seed 5005:

1. 2023-07-09 through 2023-07-15
2. 2024-01-15 through 2024-01-21
3. 2023-09-08 through 2023-09-14

Week 2 is prohibited until Week 1 passes the frozen promotion gate. The 91-day
run is prohibited until all three weeks pass.

## v1 — Liquidity Response Transition

Authoritative first-week workflow: GitHub Actions run `31068009853`, commit
`4ac49064a1dad6ee942235438b13985dde2b35eb`.

| Metric | Cost-after Nautilus result |
|---|---:|
| Starting NAV | 100,000.00 USDT |
| Ending NAV | 91,677.09301799 USDT |
| Total return | -8.322906982% |
| Geometric daily NAV growth | -1.233721357% |
| Maximum drawdown | 8.692658843% |
| Trades / wins | 7 / 1 |
| Win rate | 14.2857% |
| Profit factor | 0.378326 |
| Liquidations | 0 |

The candidate failed and was not promoted. Six entries were the rejection
branch and one was acceptance. Acceptance lost. Five rejection trades reached
structural stops, one timed out, and only one reached its target. The initial
implementation also generated one rejected forced-exit order; this was traced
to repeated close submission and is guarded by a single `exit_pending` state in
v2.

### Controlled causal diagnosis

The diagnostic rerun at commit `fe8007399bd7848b4e11bc517bde7e13fdae4876`
changed no strategy condition and preserved completed bars/features for an
observational event study. This study is not a substitute backtest and makes no
performance claim; it only selected the next state-machine hypothesis.

The observed failure was structural:

- one-minute pivot pools produced 1,661 confirmed pools and 125 classified
  sweeps in one evaluation week;
- acceptance classifications reversed against their supposed continuation at
  15–60 minute horizons;
- rejection direction was better, but actual entries bought or sold the close
  of displacement after price had already moved roughly 1.5–5.9 one-minute ATR;
- therefore additional score filters would not repair the entry geometry.

A single controlled family was changed for v2: the liquidity-event scale and
entry transition. Completed five-minute pivots replace one-minute pivots;
acceptance is removed; rejection must break the opposite extreme of the sweep
bar; entry is a resting first-retrace limit at 50% of the sweep-extreme to
CHoCH-close impulse. The structural stop remains beyond the swept extreme.

In the observational screen, five-minute rejection -> CHoCH -> first 50%
retrace produced 29 fillable candidates before the one-position constraint:
12 reached a cost-aware 2R target first, 16 reached invalidation first, and one
remained unresolved. That relation justified a Nautilus implementation; it is
not counted as validated trading performance.

## v2 — Liquidity Response Retrace

State machine:

```text
completed 5m confirmed pivot
  -> past-known liquidity pool
  -> causal pool access
  -> aggressive sweep flow + inefficient impact + depth refill + reclaim
  -> opposite CHoCH/displacement within 8 completed 1m bars
  -> submit Nautilus LIMIT bracket at first 50% impulse retrace
  -> cancel unfilled after 20 bars or if swept extreme invalidates
  -> structural SL / cost-after 2R TP / funding-safe intraday exit
```

Frozen implementation choices are causal hypotheses rather than fitted score
weights:

- completed five-minute pivot span: 2 bars on each side;
- rejection response thresholds retained from v1;
- CHoCH body at least 0.25 ATR, directional flow at least 0.04, response
  efficiency at least 0.15;
- exact first 50% retrace limit, 20-bar opportunity lifetime;
- stop 0.08 ATR beyond the sweep extreme;
- target calculated for 2.0R after modeled entry/exit costs;
- full current NAV and fixed 3% planned-loss budget;
- at most one new-entry order or open position.

The authoritative v2 result must come from the committed workflow artifact.
This section intentionally does not infer success from the observational screen.

## Known failure conditions

- Binance public `bookDepth` reports aggregated percentage-band notional rather
  than exact queue position. A stale observation invalidates classification.
- One-minute OHLC replay cannot identify every within-bar tick sequence. The
  fixed Nautilus adaptive high/low ordering, latency, fee model, bracket
  lifecycle, and liquidation path are mandatory.
- A bar that crosses both sides is an unresolved volatility shock, not two
  trades.
- A pool is consumed on first access even when no trade follows; the strategy
  may not wait for a favorable later interpretation of the same liquidity.
- A resting limit may miss a valid move. Missed entry is a defined scenario
  outcome, not permission to chase at market.
- Acceptance continuation remains disabled because its first-week causal
  direction failed. It may not be re-enabled without an independent structural
  hypothesis and a new precommitted experiment.

## v2 authoritative result and rejection

Authoritative first-week workflow: GitHub Actions run `31070088395`, commit
`cfd255da4dc75c64b455219803c023c05fef4a62`.

| Metric | Cost-after Nautilus result |
|---|---:|
| Ending NAV | 93,486.36918722 USDT |
| Total return | -6.51363081278% |
| Geometric daily NAV growth | -0.957593370% |
| Maximum drawdown | 19.694656043% |
| Trades / wins | 14 / 4 |
| Win rate | 28.5714% |
| Profit factor | 0.744210 |
| Liquidations / rejected orders | 0 / 0 |

The entry-timing repair increased opportunities and wins, but the first-week
promotion gate still failed. The execution path was internally coherent: all
14 positions and 58 orders were owned by NautilusTrader, no feature was stale,
no liquidation occurred, and the one-intent/one-position invariant held.

Trade-path diagnosis found that nine of the ten losing positions never reached
positive cost-after R. Lowering the target would therefore not repair the
candidate. The remaining structural defect was liquidity hierarchy: 682
completed five-minute swing pools and 538 pool accesses were still internal
noise rather than external liquidity events.

## v3 — 15-minute external liquidity hierarchy

One family changes from v2: completed liquidity-event scale. Fifteen-minute
confirmed swing extremes replace five-minute swing extremes. Rejection
classification, CHoCH thresholds, exact 50% resting limit, 20-bar lifetime,
structural stop, cost-after 2R target, 3% NAV loss budget, funding handling, and
Nautilus execution remain unchanged.

This is a direct test of the SMC distinction between internal and external
liquidity. It is not a new score filter and it is not permitted to change the
frozen Week-1 dates.
