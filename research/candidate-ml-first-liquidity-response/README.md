# ML-first causal liquidity-response synthesis

This candidate preserves the strongest causal decision object from the candidate-4
liquidity-route branch and replaces its weak target realization decision.

The deterministic layer owns:

`liquidity source -> accepted/failed auction -> price-volume control -> defended return entry -> structural stop -> first live opposing structural route`

The ML layer does not invent direction, entry, stop, or a target outside that route. It
estimates target-before-stop reachability for immutable checkpoints on the known route,
chooses exactly one checkpoint before entry, and compares its post-cost 3%-risk log-NAV
utility with cash. BTCUSDT, ETHUSDT, SOLUSDT, and XRPUSDT share one model without symbol
identity or absolute price features. The inherited one-account stream is routed online,
with one trade per causal parent episode and no partial entry/exit or forced time exit.

Run:

```bash
python research/candidate-ml-first-liquidity-response/research_policy.py \
  --trades research_results/candidate_ml_first/imported_candidate4_short_v2/all_global_trades.csv \
  --output artifacts/candidate_ml_first/liquidity_response_v1
```

The first two chronological windows warm the model. The first causally predicted window
sets the frozen decision gate. The remaining windows are sequential short evaluations;
the last remains untouched by variant selection.
