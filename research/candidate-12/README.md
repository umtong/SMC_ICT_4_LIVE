# Candidate 12 — Causal Liquidity Acceptance/Rejection (CLAR)

## Status

This branch is an independent candidate.  It is not a claim that the project target has been met until the committed NautilusTrader account-NAV evidence says so.

## Causal thesis

A visually detected sweep, FVG, or structure break is not itself a trade.  Candidate 12 first maintains a ledger of **live, causally confirmed liquidity pools** from completed 15-minute pivots, prior completed four-hour auctions, and prior completed UTC days.  A trade can emerge only from this sequence:

1. price reaches a previously live external pool;
2. the auction is classified as either **rejection** or **acceptance**;
3. rejection requires reclaim plus absorption/counter-flow and then an internal market-structure shift;
4. acceptance requires multiple closes beyond the pool plus displacement/aggressor alignment and then a boundary retest/reacceleration;
5. entry, invalidation, and target must still provide positive costed structural R to another live pool.

This separates pattern detection from scenario decisions and gives every state a causal `event_time_ns` and `observed_time_ns`.

## Execution and risk

- Market entry after confirmation, post-only limit target, stop-market invalidation.
- NautilusTrader exclusively owns matching, contingent order lifecycle, fees, margin, positions, and account NAV.
- Planned loss budget is current full-account NAV × 3%.
- Quantity is the rounded-down quotient of that budget and the expected per-unit loss, including entry/stop effective costs and adverse tick allowance.
- No model score multiplier, nominal cap, or separate strategy leverage cap is added.
- The strategy submits only while the instrument is flat and has no working order.  A multi-symbol portfolio allocator is added only after BTC demonstrates post-cost alpha; it must enforce one global slot across BTC/ETH/SOL/XRP.

## Frozen evaluation design

`config.json` fixes six seven-day starts before data download using seed `2026080712`.  W1–W3 are diagnostic weeks; W4–W6 remain untouched until the diagnostic gate justifies advancement.  The runner downloads immutable Binance USD-M daily one-minute archives, stores URL/size/SHA-256, shifts archive open timestamps by one minute so OHLC and flow are visible only at close, and runs the same strategy code through NautilusTrader.

## Reproduction

Inside the project Codespace/dev container:

```bash
smc4 doctor
python -m unittest discover -s research/candidate-12 -p 'test_*.py' -v
python research/candidate-12/run.py \
  --symbol BTCUSDT \
  --week W1 \
  --output artifacts/candidate-12/BTCUSDT-W1
```

Expected evidence:

```text
run.json
metrics.json
scenario_events.jsonl
submitted_plans.json
order_lifecycle.json
orders.csv
positions.csv
account.csv
data_manifest.json
```

## Decision rule

Implementation errors are repaired.  Logic failures are not disguised by loosening many filters at once.  The event ledger is used to identify whether the bottleneck is pool formation, auction classification, confirmation, costed target availability, execution, or actual outcome.  A diagnostic candidate advances only if trading frequency and post-cost expectancy create a structural path to the project target across more than one frozen week.
