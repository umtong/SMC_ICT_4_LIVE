# Candidate 11 — Continuous SCDAM Core FAR Development

This directory separates **temporary tests** from evidence that can change a
research decision.

## What is temporary

Unit tests, source-materialization checks, short smoke runs and ablations on
opened data exist only to find implementation defects, causal ordering errors or
an incoherent scenario. They cannot advance a candidate, restore holdout status
or support an alpha/success claim.

## What matters here

The research object is one complete market scenario, not a candle pattern or a
weekly NAV number:

```text
completed regional liquidity pool
-> sweep
-> completed peer-market transfer
-> local reclaim / structure shift / displacement
-> executable FAR entry
-> structural target and invalidation
-> one global-liquidity-cycle time invalidation
```

AAC and both Session-I7 routes are excluded. Opened evidence showed that pooling
these economically different states hid domain-specific failures. The detector
may still observe AAC, but the development account admits only `SCDAM_CORE/FAR`.

## Evidence role

D1-D3 are three precommitted continuous 28-day **development-gate** blocks. Each
uses one NautilusTrader account/state timeline, current-NAV 3% planned-loss
sizing and one global pending-entry/position slot. A two-day resolution tail
allows orders and positions to reach the frozen target, stop or 24-hour time
invalidation without a block-end mark exit.

The aggregate gate evaluates independent economic clusters, direction coverage,
leave-one-cluster-out growth, concentration, costs and safety. Passing only
authorizes a separately byte-frozen fresh-validation candidate. It never sets
`success_claim=true`.

## Reproduction

```bash
python research/candidate-11/core_far_continuous_v1/run_block.py \
  D1 research/candidate-11/core_far_continuous_v1/results/D1

python research/candidate-11/core_far_continuous_v1/aggregate.py \
  --results research/candidate-11/core_far_continuous_v1/results \
  --protocol research/candidate-11/core_far_continuous_v1/protocol.json \
  --output research/candidate-11/core_far_continuous_v1/aggregate.json
```
