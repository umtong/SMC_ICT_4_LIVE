# V11 frozen third-week failure analysis

## Disposition

`candidate-03-nt-lvcfr-v11-scenario-aware-protection` is rejected as a
complete candidate. It remains useful as a source of validated components, but
must not be represented as a generalizable or live-ready system.

## Implementation versus logic

The third validation week was a valid logical/generalization failure, not an
implementation failure.

- Frozen Git blob identities matched the development-passed V11 source.
- `smc4 doctor` confirmed Python 3.13.5 and NautilusTrader 1.230.0.
- The same native BacktestNode, order, fill, fee, funding, margin, position and
  Portfolio NAV path completed normally.
- All seven positions were closed; there were no rejected entries, unfinished
  positions or single-slot violations.
- Risk sizing remained the project-prescribed 3% of current native NAV.

Therefore the economic result is admissible evidence against the V11 logic.

## Frozen evidence

| Week | Final NAV | Daily geometric growth | Episodes | Win rate | MDD | Gate |
|---|---:|---:|---:|---:|---:|---|
| 2024-01-08 development | 121,354.42 | +2.80351% | 9 | 100.00% | 4.645% | pass |
| 2025-06-23 development | 108,431.20 | +1.16308% | 8 | 75.00% | 2.862% | pass |
| 2022-05-16 frozen validation | 95,881.64 | -0.59899% | 7 | 57.14% | 9.451% | fail |

The third week produced four winners and three losers, yet the full-stop losses
were much larger than the structural-target winners. Profit factor was 0.555
and mean episode PnL was -588.34 USDT.

## Largest failure mechanisms

### 1. VALUE_EDGE continuation is not a complete market state

Four VALUE_EDGE episodes produced two winners and two near-full-loss losers.
One losing long first advanced +0.666R before reversing to -0.995R. The other
losing short never developed favorable executable excursion and stopped at
-0.979R. The same label therefore combined at least two distinct auction
sequences:

- genuine directional repricing after liquidation;
- temporary displacement inside an unresolved or opposite inventory cycle.

The prior dealing-range external was useful as a protection level when reached,
but it was too remote to distinguish these sequences before a full initial
loss.

### 2. RANGE_MIGRATION reclaim has asymmetric reward

The three migration-reclaim reversals produced +0.179R, +0.411R and -1.000R.
The losing short entered close to its event-extreme target while retaining a
full structural invalidation distance. Two winners could not offset one normal
loss. This is not repaired by tighter execution or more precise accounting; it
is a scenario payoff defect.

### 3. Opportunity density is structurally insufficient

The frozen week began with 19 V1 liquidation-vacuum events, but the V7/V11 state
router admitted only seven. Even perfect avoidance of all three losses would
leave approximately +4.5% gross account growth, below the +7.21% weekly NAV
multiple corresponding to 1% daily geometric growth. Adding filters cannot
solve this deficit.

## Required one-variable ablation

The entire `VALUE_EDGE_CONTINUATION` branch was removed while retaining every
other detector, state, order, stop, target, risk and execution rule.

| Metric | Frozen V11 | Remove VALUE_EDGE |
|---|---:|---:|
| Episodes | 7 | 3 |
| Win rate | 57.14% | 66.67% |
| Final NAV | 95,881.64 | 98,722.53 |
| Net return | -4.118% | -1.277% |
| Daily geometric growth | -0.599% | -0.184% |
| Mean episode PnL | -588.34 | -425.82 |
| MDD | 9.451% | 3.589% |

Removing VALUE_EDGE reduced the loss but did not create positive expectancy.
The remaining migration-reclaim branch was still negative and supplied only
three trades. Consequently V11 has no credible path through incremental
filtering or protection tuning.

## Components that worked and are retained

1. **Strong/weak reclaim sequencing.** Requiring a completed opposite
   displacement for a strong reclaim and treating weak reclaim as a pending
   state was materially better than immediate binary reversal.
2. **Failed-reclaim reacceptance.** A weak reclaim can transition back to the
   original direction instead of being forced into a reversal.
3. **Scenario-aware protection semantics.** A prior-range boundary can be an
   invalidation for one scenario while an event extreme or equilibrium is only
   an intermediate waypoint for another.
4. **Causal structural objectives.** Event extreme, prior equilibrium and prior
   external are interpretable liquidity destinations; they should remain state
   variables, not arbitrary R-multiple replacements.
5. **Native infrastructure.** The checksum-verified public data and
   NautilusTrader-native execution/accounting path remain valid and are reused.

## Next hypothesis

V12 must be a new state-space candidate, not a V11 parameter patch. Research
will inspect the twelve V1 events that V11 left untraded and construct states
from the sequence of displacement, open-interest contraction, spot/futures
aggression, boundary acceptance/rejection and post-shock liquidity recovery.
The goal is to add economically independent opportunity families and improve
payoff asymmetry, while preserving the frozen 3% NAV risk budget and one-slot
portfolio contract.
