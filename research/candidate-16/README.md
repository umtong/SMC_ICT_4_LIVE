# Candidate 16 — Effort/Result Auction Router

Candidate 16 begins with external research, then audits the project, then implements the smallest distinct hypothesis on the existing NautilusTrader stack.

## Core policy

```text
active causal liquidity pool
  -> boundary interaction
  -> maximum 3 completed bars of effort/result observation
       -> FAILED_AUCTION: high effort, low progress, completed reclaim
       -> ACCEPTANCE_CONTINUATION: two outside closes, efficient progress
       -> UNRESOLVED: no trade
  -> preserve same-leg geometry
  -> require an unconsumed liquidity objective after costs
  -> 3% current-NAV planned loss; one global position/entry intent
```

## Files

- `EXTERNAL_RESEARCH.md`: exact queries, source URLs, access limits, adopted/rejected rules, code mapping.
- `PROJECT_AUDIT.md`: project-wide evidence and why this candidate differs.
- `PRE_REGISTRATION.md`: frozen first interval and failure interpretation.
- `effort_result_router.py`: pure terminal-state logic.
- `strategy.py`: NautilusTrader adapter and natural-objective gate.
- `candidate.py`: thin wrapper around Candidate 05’s runner.
- `config.json`: frozen v1 risk, costs, and structural thresholds.
- `tests/`: symmetry, exclusivity, causality, and sizing-policy contracts.

## Validity boundaries

The first workflow is bar-based screening with causal aggregate-trade/public-depth features. It is not tick-level queue-position proof. No performance claim is made until committed GitHub Actions evidence is inspected. Any result-driven rule change reclassifies that interval as development data.
