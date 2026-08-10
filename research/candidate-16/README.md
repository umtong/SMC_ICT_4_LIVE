# Candidate 16

Candidate 16 studies cross-asset residual dislocations and their tradeable state transitions on the project universe.

## Current active experiment: v9 role-separated residual state

v9 changes one economic role relative to v8: a prior-only robust residual inflection with non-expanding 15-minute open interest freezes the state, while state-bar flow, efficiency, notional burst and displayed depth are diagnostic only. The unchanged v8 handler still requires a strictly later residual, price, relative-return, aggressor-flow and depth convergence before a FOK price-capped entry.

The experiment reuses the existing Candidate 05 one-account four-symbol NautilusTrader runner, current-NAV 3% planned-loss sizing, realistic configured costs, natural liquidity targets and the final audited global entry slot. The inherited position-building-balance family is disabled in the diagnostic account, and any non-v9 entry path is fatal.

Run contract tests:

```bash
PYTHONPATH=research/candidate-05:research/candidate-16 \
python -m unittest discover -s research/candidate-16/tests \
  -p 'test_strategy_v9_role_separation_contract.py' -v
```

Run a period:

```bash
PYTHONPATH=research/candidate-05:research/candidate-16 \
python research/candidate-16/run_candidate_v9.py \
  --winner-evidence path/to/v9.json \
  --build-start YYYY-MM-DD --build-end YYYY-MM-DD \
  --evaluation-start YYYY-MM-DD --evaluation-end YYYY-MM-DD \
  --cache .cache/candidate-16-v9 --output artifacts/candidate-16-v9
```

The paired workflow compares v8 control and v9 over four development weeks and writes every state, non-trade and trade episode to `episodes.csv`; headline gates do not decide the diagnosis.
