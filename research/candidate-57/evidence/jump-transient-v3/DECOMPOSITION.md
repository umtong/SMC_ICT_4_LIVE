# Candidate 57 — jump transient weak-reversion decomposition v3

This is not a pass/fail tournament. The impulse-extreme stop and source entry are held fixed. Each variant changes only the causal arm/escape state transition that distinguishes weak reversion failure from a strong source-confirming escape.

| variant | trades/day | GP R/day | GL R/day | net R/day | avg win R | avg loss R | protection exits | gmean/day | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `arm0p4_escape1p0` | 0.714 | 0.146 | 0.219 | -0.073 | 0.340 | -0.765 | 5 | -0.235% | 9.7% |
| `arm0p4_escape1p25` | 0.714 | 0.146 | 0.219 | -0.073 | 0.340 | -0.765 | 5 | -0.235% | 9.7% |
| `arm0p5_escape1p0` | 0.714 | 0.280 | 0.286 | -0.006 | 0.784 | -0.800 | 3 | -0.051% | 13.0% |
| `impulse_control` | 0.714 | 0.361 | 0.364 | -0.003 | 1.684 | -0.728 | 0 | -0.051% | 13.8% |

## Exact control-relative mechanism effects

- `arm0p4_escape1p0`: winner sign preservation 100.0%; winner R preservation 35.1%; losses repaired 3; losses reduced 2; losses worsened 2; matched ΔR -0.978; new episodes 0 (0.000R).
- `arm0p4_escape1p25`: winner sign preservation 100.0%; winner R preservation 35.1%; losses repaired 3; losses reduced 2; losses worsened 2; matched ΔR -0.978; new episodes 0 (0.000R).
- `arm0p5_escape1p0`: winner sign preservation 100.0%; winner R preservation 75.3%; losses repaired 2; losses reduced 1; losses worsened 4; matched ΔR -0.041; new episodes 0 (0.000R).

## Mechanism reads

### `arm0p4_escape1p0`
- This remains a rare-event family. Low density is an integration constraint, not evidence that its high-payoff mechanism is worthless.
- Protection changed 5 realized exits; its effect is a management mechanism rather than an entry filter.
- Most control winners keep the same sign, so the repair largely preserves the observed alpha engine.
- The repair materially truncates winner R even when winner signs survive; apparent drawdown improvement may be purchased by damaging the payoff engine.
- Multiple exact control loss episodes improve, evidence that the identified giveback mechanism is causally repairable rather than a single lucky trade.

### `arm0p4_escape1p25`
- This remains a rare-event family. Low density is an integration constraint, not evidence that its high-payoff mechanism is worthless.
- Protection changed 5 realized exits; its effect is a management mechanism rather than an entry filter.
- Most control winners keep the same sign, so the repair largely preserves the observed alpha engine.
- The repair materially truncates winner R even when winner signs survive; apparent drawdown improvement may be purchased by damaging the payoff engine.
- Multiple exact control loss episodes improve, evidence that the identified giveback mechanism is causally repairable rather than a single lucky trade.

### `arm0p5_escape1p0`
- This remains a rare-event family. Low density is an integration constraint, not evidence that its high-payoff mechanism is worthless.
- Protection changed 3 realized exits; its effect is a management mechanism rather than an entry filter.
- Most control winners keep the same sign, so the repair largely preserves the observed alpha engine.
- Multiple exact control loss episodes improve, evidence that the identified giveback mechanism is causally repairable rather than a single lucky trade.

### `impulse_control`
- The gross winner engine is large enough to be strategically important at the project 3% risk budget; management must not be judged only by net PnL.
- This remains a rare-event family. Low density is an integration constraint, not evidence that its high-payoff mechanism is worthless.

## Research decision

Freeze `arm0p5_escape1p0` as the current management repair, then run it once on a fresh untouched short interval before any broader expansion.

The decision concerns this state transition only. It neither authorizes long evaluation nor ranks unrelated low- or high-frequency families.
