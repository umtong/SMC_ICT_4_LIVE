# Candidate 57 — jump management mechanism decomposition v2

This is not a pass/fail tournament. The impulse-extreme stop is held fixed and each variant changes only the giveback-management subsystem (except the explicitly labelled confirmation control).

| variant | trades/day | GP R/day | GL R/day | net R/day | avg win R | avg loss R | protection exits | gmean/day | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `confirm5_be_0p5` | 0.357 | 0.000 | 0.207 | -0.207 | 0.000 | -0.579 | 2 | -0.627% | 11.6% |
| `impulse_be_0p5` | 0.714 | 0.033 | 0.286 | -0.253 | 0.092 | -0.800 | 5 | -0.767% | 16.0% |
| `impulse_control` | 0.714 | 0.361 | 0.364 | -0.003 | 1.684 | -0.728 | 0 | -0.051% | 13.8% |
| `impulse_lock_1p0_0p25` | 0.714 | 0.082 | 0.364 | -0.282 | 0.383 | -0.728 | 3 | -0.858% | 15.3% |
| `impulse_trail_1p0_gap0p75` | 0.714 | 0.161 | 0.364 | -0.203 | 0.750 | -0.728 | 3 | -0.628% | 14.0% |

## Exact control-relative mechanism effects

- `confirm5_be_0p5`: winner sign preservation 0.0%; winner R preservation 0.0%; losses repaired 0; losses reduced 2; losses worsened 1; matched ΔR -4.320; new episodes 0 (0.000R).
- `impulse_be_0p5`: winner sign preservation 100.0%; winner R preservation 6.9%; losses repaired 2; losses reduced 2; losses worsened 3; matched ΔR -3.498; new episodes 0 (0.000R).
- `impulse_lock_1p0_0p25`: winner sign preservation 100.0%; winner R preservation 22.7%; losses repaired 0; losses reduced 4; losses worsened 2; matched ΔR -3.905; new episodes 0 (0.000R).
- `impulse_trail_1p0_gap0p75`: winner sign preservation 100.0%; winner R preservation 44.5%; losses repaired 0; losses reduced 2; losses worsened 5; matched ΔR -2.803; new episodes 0 (0.000R).

## Mechanism reads

### `confirm5_be_0p5`
- This remains a rare-event family. Low density is an integration constraint, not evidence that its high-payoff mechanism is worthless.
- Protection changed 2 realized exits; its effect is a management mechanism rather than an entry filter.
- The repair materially truncates winner R even when winner signs survive; apparent drawdown improvement may be purchased by damaging the payoff engine.
- Multiple exact control loss episodes improve, evidence that the identified giveback mechanism is causally repairable rather than a single lucky trade.

### `impulse_be_0p5`
- This remains a rare-event family. Low density is an integration constraint, not evidence that its high-payoff mechanism is worthless.
- Protection changed 5 realized exits; its effect is a management mechanism rather than an entry filter.
- Most control winners keep the same sign, so the repair largely preserves the observed alpha engine.
- The repair materially truncates winner R even when winner signs survive; apparent drawdown improvement may be purchased by damaging the payoff engine.
- Multiple exact control loss episodes improve, evidence that the identified giveback mechanism is causally repairable rather than a single lucky trade.

### `impulse_control`
- The gross winner engine is large enough to be strategically important at the project 3% risk budget; management must not be judged only by net PnL.
- This remains a rare-event family. Low density is an integration constraint, not evidence that its high-payoff mechanism is worthless.

### `impulse_lock_1p0_0p25`
- This remains a rare-event family. Low density is an integration constraint, not evidence that its high-payoff mechanism is worthless.
- Protection changed 3 realized exits; its effect is a management mechanism rather than an entry filter.
- Most control winners keep the same sign, so the repair largely preserves the observed alpha engine.
- The repair materially truncates winner R even when winner signs survive; apparent drawdown improvement may be purchased by damaging the payoff engine.
- Multiple exact control loss episodes improve, evidence that the identified giveback mechanism is causally repairable rather than a single lucky trade.

### `impulse_trail_1p0_gap0p75`
- This remains a rare-event family. Low density is an integration constraint, not evidence that its high-payoff mechanism is worthless.
- Protection changed 3 realized exits; its effect is a management mechanism rather than an entry filter.
- Most control winners keep the same sign, so the repair largely preserves the observed alpha engine.
- The repair materially truncates winner R even when winner signs survive; apparent drawdown improvement may be purchased by damaging the payoff engine.
- Multiple exact control loss episodes improve, evidence that the identified giveback mechanism is causally repairable rather than a single lucky trade.

## Research decision

No management repair preserves enough of the payoff engine while improving repeated loss episodes; retain impulse control and move to delayed rejection/re-entry anatomy.

The decision concerns the management mechanism only. It does not authorize long evaluation or imply that other low- or high-frequency families should be discarded.
