# EasyChart ML2

ML2 keeps the deterministic EasyChart/RE1 scenario engines responsible for the part that must remain interpretable and structurally exact:

```text
market location and causal episode
→ direction
→ entry
→ structural invalidation
→ pre-existing objective
→ gross planned RR ≥ 1.0
```

Only after that immutable plan exists does ML estimate `P(target before stop | decision-time state)`. NautilusTrader remains responsible for one continuous account, one global position, full entry/full exit, fees, fills, protective orders and quantity sizing for approximately 3% NAV loss at the frozen stop. ML cannot change risk, entry, stop or target.

## Why ML is used here

RE1 contains useful market logic but several broad-context decisions are boolean. The same common-market move can mean continuation in one local auction and exhaustion/absorption in another. A fixed gate loses those interactions, while a model that invents arbitrary orders is difficult to audit and unsafe. ML2 therefore learns only the conditional quality of already-complete plans.

User-supplied trade examples and desired performance characteristics are diagnostic clues, not filters, labels or optimization targets. The classifier objective is proper probabilistic log loss on target-before-stop first passage. Trade frequency, win rate and NAV are consequences examined in the integrated continuous account, never labels the model is trained to imitate.

## Candidate generation without selected-sample bias

RE1 consulted the four-market common factor both in final routing and inside some continuation engines. Opposing context could therefore destroy a formation or consume its first touch before a complete plan was observable. ML2:

- keeps structural state, first-return ownership, target freshness and duplicate causal-episode ownership;
- preserves scenarios whose creation genuinely depends on the common factor;
- neutralizes only broad quality vetoes in local continuation, efficient pullback and macro-trend pullback;
- records the true factor at setup formation, immediately before response, and at plan emission;
- reconstructs the inherited veto in shadow mode, while select mode lets the calibrated model judge the observed interaction.

This widens the learning sample without loosening plan geometry.

## Causal feature contract

The fixed 169-feature schema is numeric and symbol-agnostic. It contains:

- plan geometry, timing and the real `higher_strength_ratio`, `lower_strength_ratio`, `trigger_strength_ratio` fields;
- causal family and scenario path;
- direct plan-specific OB, FVG, horizontal, diagonal, channel, sweep, retest, flow and pullback mechanisms;
- role-specific zone kinds and retrieved higher/lower/trigger/target age, width, strength, touch state and distance;
- macro/common-factor state at three causal times;
- completed 1/5/15/60-minute trend, wick, volatility and volume state using prior-only baselines;
- aggressor-flow initiative and adverse-flow absorption proxies;
- synchronized BTC/ETH/SOL/XRP common move, residual, breadth and relative rank;
- UTC cyclical timing and a small number of market-mechanism interactions.

`rule_provenance` is deliberately excluded from feature and hidden-veto classification because the inherited contract can contain process-global curriculum rules unrelated to the specific plan.

## Label and model

Post-run future bars label each frozen plan:

- `TARGET_FIRST = 1`
- `STOP_FIRST = 0`
- target and stop in the same one-minute bar = `0`
- unresolved = right-censored and excluded from binary fitting by default

The observed R outcome is reconstructed from the exact win/loss economics recorded before entry, so training diagnostics use the same fee/slippage assumptions as runtime selection.

CatBoost fits only the chronological training segment with causal-event unit weights. A separate later segment fits Platt calibration. Label intervals crossing the next segment are purged with an embargo. Symbol identity is not a feature. There is no target win-rate loss, trade-frequency quota, confidence threshold or example-imitation objective.

For calibrated probability `p`, runtime computes:

```text
win_log  = log(1 + 0.03 × win_net_R)
loss_log = log(1 + 0.03 × loss_net_R)
expected_log_growth = p × win_log + (1-p) × loss_log
```

A plan is selectable only when expected log growth is positive. Simultaneous complete candidates are ranked by expected log growth. Position size remains the inherited fixed-risk quantity.

## Files

- `candidate_bundle_ml2.py` — broad but structurally unchanged candidate generation.
- `ml2_context.py` — causal common-factor history and inherited hidden-veto reconstruction.
- `ml2_features.py` — 169-feature causal state and reusable family classification.
- `ml2_model.py` — fixed-plan economics, expected-log decision and checksum-bound CatBoost runtime.
- `execution_ml2.py` — Nautilus binding, shadow reconstruction and global-slot arbitration.
- `run_mtf_backtest_ml2.py` — one continuous BTC/ETH/SOL/XRP account runner.
- `harvest_ml2.py` — post-run first-passage labeling with a future tail.
- `build_ml2_dataset.py` — exact plan-identity feature/label join.
- `merge_ml2_datasets.py` — non-overlapping chunk merge with process-local ID namespacing.
- `train_ml2.py` — chronological CatBoost fitting, calibration, purge, export and diagnostics.
- `selfcheck_ml2.py`, `tests/` — causal contracts and optional end-to-end model round trip.

## Research execution

The quick GitHub workflow runs a short four-symbol shadow account to expose implementation or geometry errors. The medium workflow is triggered only by changing `TRAINING_RUN`; it harvests disjoint market periods in parallel, merges them chronologically, trains once and exports a fixed model. A later select workflow must use that fixed artifact on a later untouched continuous account.

Classifier metrics and counterfactual candidate summaries are not substitutes for the final result. The evidence that matters is the actual Nautilus trade tape and continuous NAV after costs, under the single global position constraint.
