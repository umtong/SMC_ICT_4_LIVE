# Candidate ML-k — Causal Control Transfer V1

This policy converts the useful fragments found across the liquidity-auction branches into one decision process rather than treating OB, FVG, trend lines, channels, fakeouts, or traps as independent signals.

The common causal episode is:

1. public liquidity or a defended source attracts price;
2. order flow expends effort but cannot continue efficiently, or a coherent approach reaches a meaningful source;
3. price demonstrates directional control through displacement while opposite flow is absorbed;
4. the first return to the generated response zone is entered;
5. the closest inherited structural destination able to pay at least `1.0 net R` is used;
6. one global account slot chooses the first independent qualifying episode.

## Four independent mechanisms

`DEFENDED_BASIS_ABSORPTION` captures repeatedly defended sources where basis moves against the apparent departure while price impact per activity remains low. This is failed initiative plus absorption, not a generic low-volatility filter.

`PUSH_PULL_ABSORPTION` requires an initial directional displacement, then meaningful opposite aggressor flow late in the sequence without erasing the delivery, inside a materially wide response zone.

`EVENT_ABSORPTION_DISPLACEMENT` requires low event impact per unit activity followed by a later directional sequence block and enough stop distance that execution cost does not dominate the plan.

`EFFICIENT_APPROACH_SOURCE` captures a coherent, path-efficient and high-impact approach into a source wide enough to represent actual inventory rather than one noisy candle.

The thresholds are fixed constants in `control_transfer_router.py`. Runtime data never tunes them, no symbol identity enters the decision, and no outcome/MFE/MAE field is read before routing.

## Development replay used to freeze V1

The four available eleven-day archives were all treated as development after inspection: February 2024, August 2025, January 2026, and March 2026.

With exact action outcomes produced by the existing one-minute/Nautilus research generator, one causal episode per plan, one global pending-or-position slot, `3%` current-NAV stop risk, no scale-in/out, and ambiguous same-minute TP/SL bars counted as losses, the fixed four-family policy completed **29 trades** from **56 accepted orders**:

- 19 wins / 10 losses;
- 65.52% win rate;
- +51.65 net R;
- 3.245 average-win/average-loss ratio;
- continuous NAV 1.00 → 4.23;
- maximum drawdown 8.73%;
- every one of the four development periods was positive after costs.

This is strong development evidence, not untouched evidence. The fresh workflow evaluates the frozen policy on newly harvested dates before any threshold changes.

## Reproduction

```bash
python research/candidate-ml-k-control-transfer/control_transfer_router.py \
  --root /path/to/period-directories \
  --output research_results/candidate_ml_k/control_transfer/run
```

Each period directory must contain the generator's `departure_actions.csv.gz`. The output is `summary.json`, `selected_orders.csv`, and `closed_trades.csv`.
