# Candidate 10 v24 — Controlled Implementation Repairs Before Verdict

No performance conclusion is drawn from the runs in this document. The v24
market hypothesis, week, seed, detector thresholds, event grammar, entry, stop,
target, costs and 3% current-NAV risk remained frozen throughout.

## 1. Natural no-trade intervals misclassified as data gaps

- Workflow: `candidate-10-v24-research`
- Run: `31161666092`
- Job: `92813173542`
- Executed source: `d0be56f347693d24841d4a20f09b903f34017f31`
- Classification: **IMPLEMENTATION_ERROR**

The original aligner intersected only non-empty spot and perpetual five-second
aggregate-trade buckets. Twenty intervals in the eight-day warmup/evaluation
source had no trade on one venue. These are valid zero-flow market states, not
missing records. Their omission made the common timestamps appear
non-continuous and stopped execution before any scenario or order.

### Controlled repair

`c10_v24_dense_alignment.py` now creates every completed five-second interval.
When a venue has no trade in `[t-5s,t)`:

```text
OHLC = last actual trade price observed strictly before t
quote volume = 0
taker-buy quote = 0
trade count = 0
source trade timestamp remains the last actual timestamp < t
```

This is causal carry-forward of the last observed price, not future
interpolation. Regression tests cover one-sided empty intervals, both-sided
empty intervals and rejection of any source trade timestamp at or after the
completed-row timestamp.

## 2. Impact overlay imported after execution class definition

- Superseded workflow run: `31162200207`
- Executed source: `d5ff4d914c055d0f1b39135e2af1014c65da9759`
- Outcome: intentionally cancelled by the next controlled commit
- Classification: **IMPLEMENTATION_ERROR FOUND BY STATIC AUDIT**

The dense launcher initially imported `c10_v24_research` before installing the
fixed-point impact overlay. Importing the research module also imported the live
cost ledger and v24 strategy, freezing their class bases. A run with no trade
could therefore appear clean while the actual trading class inherited the raw
base strategy rather than `ImpactControlledLiquidationStrategy`.

### Controlled repair

The launcher now installs `v20_impact_control` before importing any v24 research,
ledger or strategy module. `test_v24_install_order.py` starts a fresh process and
requires the strategy MRO to contain, in order:

```text
CrossMarketCandidate10Strategy
→ LiveCostLiquidationStrategy
→ ImpactControlledLiquidationStrategy
→ LiquidationCandidate10Strategy
```

The active workflow copies and executes this test before market-data download or
Nautilus replay.

## 3. Valid verdict run

Only workflow run `31163050261` at source
`33669bd8df7dcefab4fcd6075743e386d52eb2c4` or a later same-logic controlled
repair can be used for the v24 performance verdict. A valid run must pass:

- verified base-source materialization;
- `smc4 doctor`;
- compilation and all inherited/current regression tests;
- dense-grid causality and raw aggregate-trade integrity;
- strategy MRO contract;
- full and exact spot-flow-removal ablation in isolated processes;
- fill-time modeled-impact ledger and per-trade 3% all-cost NAV reconciliation.
