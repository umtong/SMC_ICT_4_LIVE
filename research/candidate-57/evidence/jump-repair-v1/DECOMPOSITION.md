# Candidate 57 — 4h jump reversion structural decomposition

This is a mechanism audit, not a pass/fail gate. Each frozen variant changes one causal subsystem while keeping the public 4h jump-reversion alpha hypothesis and the 3% NAV planned-loss contract.

## System vectors

| variant | trades/day | gross profit R/day | gross loss R/day | net R/day | avg win R | avg loss R | <=5m loss share | winner horizon share | gmean/day | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_terminal_source` | 0.714 | 0.227 | 0.548 | -0.320 | 3.184 | -0.852 | 44.4% | 100.0% | -1.009% | 18.1% |
| `confirm5_residual_rank` | 0.357 | 0.073 | 0.192 | -0.119 | 0.511 | -0.898 | 0.0% | 100.0% | -0.367% | 6.8% |
| `confirm5_source` | 0.357 | 0.148 | 0.200 | -0.052 | 1.037 | -0.932 | 33.3% | 100.0% | -0.171% | 9.6% |
| `impulse_extreme_source` | 0.714 | 0.361 | 0.364 | -0.003 | 1.684 | -0.728 | 14.3% | 100.0% | -0.051% | 13.8% |
| `terminal_residual_rank` | 0.714 | 0.000 | 0.626 | -0.626 | 0.000 | -0.876 | 40.0% | 0.0% | -1.886% | 23.4% |

## Mechanism reads

### `baseline_terminal_source`
- Low-frequency but high-payoff alpha engine: preserve as a rare-event component rather than rejecting for frequency alone.
- Winner and loser paths separate strongly by holding signature: surviving initial extension often reaches the full-horizon payoff, making confirmation or structural stop repair high-leverage.
- Net evidence is concentrated in one winner; retain the mechanism but demand another untouched interval before treating the magnitude as stable.
- Opportunity density is below the final system requirement; this can still be a valuable scenario family if its marginal account contribution survives integration.

### `confirm5_residual_rank`
- Net evidence is concentrated in one winner; retain the mechanism but demand another untouched interval before treating the magnitude as stable.
- Opportunity density is below the final system requirement; this can still be a valuable scenario family if its marginal account contribution survives integration.
- Repair is too destructive to the observed winner engine; apparent PnL improvement would not identify the intended mechanism.

### `confirm5_source`
- Net evidence is concentrated in one winner; retain the mechanism but demand another untouched interval before treating the magnitude as stable.
- Opportunity density is below the final system requirement; this can still be a valuable scenario family if its marginal account contribution survives integration.
- Repair shows high leverage: it preserves most baseline winners while causally avoiding a material share of baseline loss episodes.

### `impulse_extreme_source`
- Observed gross winner engine alone is large enough to matter for the 1% NAV/day objective at 3% risk, before losses and conflicts.
- Opportunity density is below the final system requirement; this can still be a valuable scenario family if its marginal account contribution survives integration.

### `terminal_residual_rank`
- Net evidence is concentrated in one winner; retain the mechanism but demand another untouched interval before treating the magnitude as stable.
- Opportunity density is below the final system requirement; this can still be a valuable scenario family if its marginal account contribution survives integration.
- Repair is too destructive to the observed winner engine; apparent PnL improvement would not identify the intended mechanism.

## Baseline-relative causal episode comparison

- `confirm5_residual_rank`: winner preservation 0.0%; baseline loss avoidance 77.8%; matched ΔR 1.588; avoided loss 5.825R; lost winner 3.184R.
- `confirm5_source`: winner preservation 100.0%; baseline loss avoidance 55.6%; matched ΔR -0.335; avoided loss 4.098R; lost winner 0.000R.
- `impulse_extreme_source`: winner preservation 100.0%; baseline loss avoidance 0.0%; matched ΔR 4.444; avoided loss 0.000R; lost winner 0.000R.
- `terminal_residual_rank`: winner preservation 0.0%; baseline loss avoidance 55.6%; matched ΔR 0.000; avoided loss 3.979R; lost winner 3.184R.

## Next research action

{
  "all_mechanism_vectors": [
    {
      "gross_profit_r_per_day": 0.14814649007589442,
      "immediate_stop_share_reduction": 0.1111111111111111,
      "loss_avoidance": 0.5555555555555556,
      "net_r_per_day": -0.05166593349215457,
      "variant": "confirm5_source",
      "winner_preservation": 1.0
    },
    {
      "gross_profit_r_per_day": 0.36090118294246126,
      "immediate_stop_share_reduction": 0.30158730158730157,
      "loss_avoidance": 0.0,
      "net_r_per_day": -0.0029860345646092667,
      "variant": "impulse_extreme_source",
      "winner_preservation": 1.0
    },
    {
      "gross_profit_r_per_day": 0.07295682461023877,
      "immediate_stop_share_reduction": 0.4444444444444444,
      "loss_avoidance": 0.7777777777777778,
      "net_r_per_day": -0.11940884057930398,
      "variant": "confirm5_residual_rank",
      "winner_preservation": 0.0
    },
    {
      "gross_profit_r_per_day": 0.0,
      "immediate_stop_share_reduction": 0.0444444444444444,
      "loss_avoidance": 0.5555555555555556,
      "net_r_per_day": -0.6260271681630128,
      "variant": "terminal_residual_rank",
      "winner_preservation": 0.0
    }
  ],
  "most_informative_mechanism": {
    "gross_profit_r_per_day": 0.14814649007589442,
    "immediate_stop_share_reduction": 0.1111111111111111,
    "loss_avoidance": 0.5555555555555556,
    "net_r_per_day": -0.05166593349215457,
    "variant": "confirm5_source",
    "winner_preservation": 1.0
  },
  "principle": "Advance the mechanism that preserves the observed winner engine and reduces the identified loss engine; final PnL is supporting evidence, not the sole selector."
}

The next interval must remain untouched until the chosen mechanism is frozen. Long evaluation is not authorized by this diagnostic alone.
