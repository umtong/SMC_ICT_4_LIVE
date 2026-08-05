# Candidate-02 — Liquidity Cascade Reclaim

Candidate-02 is an independent complete SMC/ICT hypothesis, not a component for
another candidate. It trades the causal sequence:

```text
observable external liquidity
→ finite stop-liquidity excursion
→ close reclaim of the prior auction
→ opposite CHoCH/MSS with displacement
→ directional FVG
→ FVG retest and rejection
→ nearest opposing external liquidity
```

A liquidity sweep alone never creates an order. Every swing records its visual
pivot time and its later confirmation/observation time. Scenario transitions,
entry rejections and trade closure reasons are written to a causally validated
`scenario_events.jsonl` file.

## Structure

- `core.py`: pure causal event detectors, state machine, target geometry and NAV
  loss-budget sizing.
- `strategy.py`: one global NautilusTrader strategy for BTC/ETH/SOL/XRP with at
  most one pending entry or position.
- `backtest.py`: Binance Vision ingestion, integrity checks, instrument/cost
  definitions, NautilusTrader runs and NAV diagnostics.
- `candidate.py`: reproducible command-line entry point.
- `config.json`: the single locked scenario/risk/execution specification.
- `HYPOTHESIS.md`: pre-data rationale and predetermined random-week screen.
- `tests/`: causality, sizing and metric tests.

## Reproduce

The project Codespace/Dev Container already contains the environment.

```bash
smc4 doctor
python research/candidate-02/candidate.py self-test
python research/candidate-02/candidate.py select-windows
python research/candidate-02/candidate.py validate \
  --output artifacts/candidate-02-screen \
  --cache .cache/candidate-02/binance-vision
```

The validation command returns `0` when the locked target is met and `2` when a
complete run rejects the candidate. Runtime or data errors use other non-zero
codes.

## Required evidence

A completed run writes:

```text
artifacts/candidate-02-screen/
├── run.json
├── metrics.json
├── data_manifest.json
├── selected_windows.json
├── scenario_events.jsonl
├── discovery/
├── confirmation-a/
└── confirmation-b/
```

Each window contains orders, fills, positions, account history, signals, risk
sizing, trade lifecycle, marked NAV and scenario events. The branch workflow
runs the same path and preserves the directory as a GitHub Actions artifact.

## Known a-priori failure modes

The hypothesis should fail rather than trade when an excursion is accepted
outside the pool, reclaim is slow, displacement does not break pre-known
structure, no directional imbalance remains, the imbalance closes through,
the nearest opposing liquidity gives inadequate geometry, cross-asset capacity
is occupied, or the thesis exceeds its intraday holding horizon. Empirical
failure conditions and the promotion decision are added after the locked
screen.
