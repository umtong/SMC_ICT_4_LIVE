# EasyChart ML Thinking

This branch keeps the existing EasyChart causal scenario engines and the audited NautilusTrader account/order lifecycle. ML is inserted only at the unresolved human-discretion point: several complete, pre-entry plans exist, but the bot must decide which current context is most likely to reach its fixed target before its fixed structural stop.

## Decision policy

1. The RE1 flow bundle emits every causal plan with immutable entry, stop and target.
2. The router consumes only plan-time geometry and the plan-associated causal flow transition.
3. A regularized logistic model estimates `P(target before stop)`.
4. The current venue assumptions produce plan-specific target and stop net returns in R.
5. The one global account slot takes the candidate with the largest positive expected net R. If every candidate is non-positive, it does not trade.

The EV boundary is not an arbitrary confidence threshold: it is exactly the probability needed to overcome that plan's fees, slippage and reward/risk geometry. Position sizing remains the existing fixed 3% NAV stop risk. There are no partial entries, partial exits, daily loss limits, dynamic stops or fallback strategies.

## Train

First run the existing RE1 flow candidate over a development interval and create the identity-safe counterfactual CSV with `counterfactual_plan_harvest_fixed.py`. Then:

```bash
python research/candidate-easychart-ml-thinking/train_ml_router.py \
  --input outputs/development/counterfactual_plans.csv \
  --output outputs/development/ml_router.json \
  --train-end-ns <LAST_DEVELOPMENT_TIMESTAMP_NS>
```

Training excludes unresolved plans, treats same-minute target/stop collisions as stops, weights sibling candidates from the same causal episode inversely, and chooses ridge strength by expanding-time log loss. The model is intentionally a small inspectable NumPy logistic model rather than a direction-predicting black box.

## Evaluate in one continuous account

```bash
python research/candidate-easychart-ml-thinking/run_mtf_backtest_ml_thinking.py \
  --ml-model outputs/development/ml_router.json \
  <the existing run_mtf_backtest_re1.py arguments> \
  --start <DATE_AFTER_DEVELOPMENT> \
  --output outputs/ml-evaluation
```

The strategy fails if an evaluated plan timestamp overlaps the model's labelled development interval. It also fails when the model is absent or incompatible; there is no silent hard-router fallback.

## Fast check

```bash
python research/candidate-easychart-ml-thinking/self_check_ml_thinking.py
```

This verifies training, time ordering, unknown categories, serialization, probability ordering, cost-aware EV and the absence of future-outcome columns from the model schema.
