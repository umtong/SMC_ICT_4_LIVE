# Candidate 14 — Event Price-Discovery Transfer Auction

Candidate 14 starts from Candidate 13's audited SCDAM detector, regional liquidity map, exact current-NAV 3% risk sizing, global one-slot allocator and NautilusTrader execution/accounting. It changes no market data, session ranges, liquidity targets, fee reserves, stop order, quantity formula or portfolio constraint.

## Research claim under test

A local failed auction is not always a reversal of the completed 24-hour auction. The same causal sequence can be one of four different market roles:

1. **COUNTERTREND_REVERSAL** — a moderate adverse auction exhausts after an external liquidity raid.
2. **TREND_RESUMPTION** — a counter-directional raid fails and the already controlling auction resumes.
3. **ORIGINATOR_TRANSFER** — one instrument completes efficient local price discovery before most peers, while at least one peer has begun to follow and visible support dominates opposition.
4. **LAGGARD_TRANSFER** — all peers have already repriced, but the candidate still completes its own efficient structural confirmation and retains an independent costed target.

The local SMC/ICT invariant remains:

```text
completed regional range
-> pre-existing external liquidity is traded through
-> reclaim or outside acceptance is observed
-> local structure displaces in causal order
-> an independent live external target remains
-> exact costed price plan and 3% NAV loss budget
```

## Why this is not threshold relaxation

Candidate 14 adds no fitted numerical threshold. It reuses the already frozen measurements:

- confirmation impulse;
- sweep-to-confirmation path efficiency;
- pre-event-volatility standardized displacement;
- synchronized peer returns;
- candidate and market trailing directional auction;
- event direction rank.

These observations are assigned to mutually exclusive economic roles rather than requiring every valid event to look like a countertrend reversal led by a large final one-minute candle.

## Execution change

FAR retains confirmation-close market entry only when taker entry, stop-market loss and maker target costs still satisfy the existing structural net-R floor. AAC now receives the same test after acceptance, defended pullback and reacceleration have all completed. If the confirmation close does not retain sufficient costed R, AAC falls back to its defended-pivot post-only limit. Signal evidence, stop and target do not change with order type.

## Validation discipline

The first protocol is explicitly `diagnostic`. It reuses Candidate 13 W10-W14 only to isolate the effect of Candidate 14 semantics. Even if the diagnostic gate passes, `success_claim` is forced to `false`.

Only after a coherent diagnostic result may the code and strategy semantics be frozen and tested on newly predeclared, non-overlapping intervals. Final success requires the full aggregate gate and all independent evidence audits.

## Reproduction

```bash
smc4 doctor
export PYTHONPATH="$PWD/research/candidate-14:$PWD/src"
python -m unittest discover -s research/candidate-14 -p 'test_*.py' -v
bash research/candidate-14/run_week.sh W10
python research/candidate-14/aggregate.py \
  --results research/candidate-14/results \
  --protocol research/candidate-14/protocol.json \
  --output research/candidate-14/aggregate.json
```

NautilusTrader remains the only backtest and account engine.
