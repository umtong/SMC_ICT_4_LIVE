# EasyChart ML System

This candidate does not optimize against an existing RE1 policy. Existing RE1 variants are treated only as reusable scenario machinery and failure evidence.

## Market decision

The system searches for complete causal auction plans from three independent mechanism owners:

1. failed auction / liquidity sweep and reclaim;
2. accepted control transfer / break, hold and response;
3. current-leg first pullback continuation.

Each owner must declare direction, entry, structural invalidation and a pre-existing objective before ML sees the plan. OB, FVG, trend line, channel and flow are evidence inside an auction episode, not unconditional entries and not equal-vote indicators.

## ML responsibility

The router receives only information available at plan emission:

- immutable plan geometry and post-cost target/stop economics;
- the plan-associated flow/control-transfer trace;
- the same incremental completed-minute local and synchronized four-symbol common market state used in research and execution.

It predicts target-before-stop probability. Symbol identity and calendar date are forbidden features. Development environments and symbols receive equal aggregate weight, alternatives from one causal interaction share weight, and inference uses the median of full, leave-one-environment and leave-one-symbol models. Calibration uses environment-held-out predictions.

Candidates are ranked by expected log NAV growth under the fixed 3% stop-risk contract. A trade is submitted only when this value is positive. There is no win-rate target, trade-count target, baseline-improvement target, partial entry/exit, stop movement or discretionary fallback.

## Files

- `opportunity_universe.py`: namespaces complete plans from distinct auction mechanisms.
- `causal_state.py`: exact shared offline/live completed-minute state.
- `augment_causal_state.py`: attaches the shared state to counterfactual plan labels.
- `robust_router.py`: environment-balanced nonlinear ensemble and log-growth decision.
- `train_robust_router.py`: multi-environment training.
- `execution_ml_system.py`: one-account NautilusTrader arbitration and execution.
- `run_mtf_backtest_ml_universe.py`: broad plan harvest.
- `run_mtf_backtest_ml_system.py`: integrated system run.
- `self_check_ml_system.py`: dependency-light causality, serialization and ranking checks.
