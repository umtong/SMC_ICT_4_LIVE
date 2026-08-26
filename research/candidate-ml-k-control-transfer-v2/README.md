# Candidate ML-k — Causal Control Transfer V2

V2 keeps the four V1 control-transfer mechanisms and expands the opportunity set with three independent market mechanisms. It was frozen on `research_candidate_ML_k` before the running `fresh1` windows produced results.

The common policy is unchanged: a public liquidity episode must demonstrate control, the first return to its generated response zone is used, the nearest inherited structural destination paying at least 1 net R is selected, and one global account slot accepts the first independent episode.

## Added mechanisms

### Opposing-flow defended source

Price reaches a repeatedly defended source even though short-horizon aggressor flow is not supporting the approach. The decision is not “low delta is bullish/bearish”; it is that price progress against the apparent flow, at a source defended at least nine times, is evidence of passive inventory and trapped initiative.

### Accumulated source with a thin route

Inventory has spent at least 26 minutes near the source and the full source-to-target profile path is classified as low volume. This captures a completed accumulation source with little intervening auction resistance rather than loosening an existing setup.

### Passive approach into open structural expansion

The 12-minute toward-direction delta share is at most 0.08, the first opposing route obstacle is at least 90 bps away, and the 60-minute structure has extended by at least 8 ATR in the trade direction. The structural test is direction symmetric: rising highs for longs and falling lows for shorts. This captures continuation after passive absorption inside an already expanding auction, with enough open path for the inherited target.

## Frozen development replay

The exact one-minute action outcomes from February 2024, August 2025, January 2026, and March 2026 were routed as one continuous account with 3% current-NAV stop risk, one pending-or-position slot, one plan per causal episode, no scaling, and ambiguous same-minute barriers counted as losses.

The frozen V2 replay completed **46 independent trades from 91 accepted orders**:

- 28 wins / 18 losses;
- 60.87% win rate;
- +65.53 net R;
- 2.98 average-win/average-loss ratio;
- continuous NAV 1.00 → 6.19;
- 11.47% maximum drawdown;
- all four periods and all four symbols were positive after costs.

Period results were +14.52R from 9 trades, +28.12R from 12 trades, +14.56R from 11 trades, and +8.33R from 14 trades. The 46 completed trades exceed the 44 calendar days represented by these four development archives without duplicate episode entries.

These archives are development evidence. The already-running `fresh1` artifacts can evaluate this frozen file without changing its thresholds, followed by additional unseen windows if its behavior persists.

## Reproduction

```bash
python research/candidate-ml-k-control-transfer-v2/control_transfer_router_v2.py \
  --root /path/to/period-directories \
  --output research_results/candidate_ml_k/control_transfer_v2/run
```

Each period directory must contain `departure_actions.csv.gz` from the existing exact episode generator.
