# Candidate 14 — Core Preservation, Leader Catch-up, Displacement Execution

Candidate 14 uses Candidate 13's audited SCDAM detector, regional liquidity map, exact current-NAV 3% risk sizing, global one-slot allocator and NautilusTrader execution/accounting. It does not alter market data, session ranges, liquidity targets, fees, order accounting or portfolio constraints.

## Development v1 was rejected

The first implementation generalized countertrend reversal into trend resumption, originator transfer, laggard transfer and path-only confirmation, and changed AAC to confirmation-close market execution. It increased closed trades from 4 to 12 but produced 5 wins and 7 losses, only 0.0110% daily geometric growth and a 6.20% weekly trade-path drawdown. That result is preserved in `development-v1-aggregate.json` and `development-v1-RESULT.md`.

The failure was logical, not an engine error: all safety audits passed. Generic transfer branches and AAC market execution were removed rather than tuned.

## Development v2 hypothesis

Candidate 13 remains the immutable core. Candidate 14 adds one cross-market state and one execution state.

### 1. Dynamic liquidity-leader catch-up

```text
candidate is the dynamic 24-hour quote-notional leader
-> candidate still lags the proposed reversal
-> a strict majority of peers already has positive direction-signed drift
-> all peers move in the proposed direction from candidate sweep to confirmation
-> candidate prints an efficient, volatility-normalized recovery path
-> candidate is not the final event laggard
-> approve LIQUIDITY_LEADER_CATCHUP
```

This is not generic originator or laggard permission. It represents transfer of peer price discovery into the deepest currently observed liquidity venue.

### 2. Confirmed displacement-failure execution

Candidate 13 sometimes approves a strong FAR scenario but leaves a passive order at the displacement void because confirmation-close entry with the original sweep-extreme stop does not retain the minimum after-cost R. If price never retraces, the valid move is missed.

After reclaim, structure shift and displacement are complete, full buffered traversal back through the known displacement void invalidates the immediate continuation. Candidate 14 therefore tests, in order:

```text
confirmation-close market + original sweep stop
-> if exact costed R fails:
confirmation-close market + full-displacement-traversal stop
-> if exact costed R or ATR floor fails:
unchanged passive void order
```

The independent external target is unchanged. Quantity is still computed from exact current NAV and a maximum planned loss of 3%, including entry/stop fees. AAC remains the original defended-pivot post-only limit because development v1 showed that immediate AAC execution surrendered too much structural reward.

## Causal invariant

```text
completed regional range
-> pre-existing external liquidity traded through
-> reclaim or outside acceptance observed
-> local structure displacement completed
-> independent live external target remains
-> cross-market semantic state approved
-> exact costed price plan
-> NautilusTrader order and account NAV
```

## Validation discipline

W10-W14 are development diagnostics already observed by Candidates 13 and 14. They can identify implementation or mechanism failure but can never support a Candidate 14 success claim. A successful development result must be frozen before newly predeclared, non-overlapping evaluation intervals are run.

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

NautilusTrader is the only backtest and account engine.
