# Candidate 14 v8 failure analysis

## Decision

`candidate-14-v8-explicit-acceptance-failure` is rejected. The branch produced valid continuous NautilusTrader evidence, but the strategy state definition was catastrophically wrong.

Evaluation: `2026-05-11` through `2026-08-03`, one continuous account, no weekly reset.

- starting NAV: `100,000 USDT`
- final NAV: `13,079.22871711 USDT`
- daily geometric growth: `-2.392515%`
- closed trades: `128`
- wins / losses: `21 / 107`
- win rate: `16.4062%`
- payoff ratio: `2.0894`
- continuous realized drawdown: `87.8807%`
- active weeks: `12 / 12`
- SCDAM / Session I7 trades: `126 / 2`

All provenance, metric recalculation, exact current-NAV 3% loss budget, global one-slot, partial-fill protection, liquidation and engine audits passed. The result is therefore admissible evidence against the v8 strategy logic.

## What v8 attempted

V8 suppressed the unresolved AAC continuation branch and tried to represent:

```text
acceptance-origin event
→ deep boundary re-entry observes failure
→ no same-bar reversal
→ later opposite initiative breaks the failure-bar extreme
→ failure-bar invalidation and live opposing external draw
```

The state-chain implementation defect in the first run was fixed without changing this strategy rule. The corrected run completed and reproduced the same economic population.

## Dominant logic error

V8 tested only `acceptance_seed`, not completion of the accepted-auction state. `acceptance_seed` means that acceptance is a possible branch. It does not prove the frozen AAC sequence:

```text
minimum outside holds
→ causal defended pullback
→ later reacceleration beyond the frozen impulse extreme
```

Consequently V8 interpreted an unsuccessful acceptance attempt as a completed acceptance which had subsequently failed. It generated 126 SCDAM reversals from 126 distinct source scenario IDs, so the failure was not duplicate IDs inside one cascade. The state itself was far too broad.

A controlled chronology comparison with the v7 event ledger found ten v8 reversal scenarios which eventually satisfied the frozen AAC completion sequence. Every v8 reversal occurred **before** that completion, by approximately `43` to `247` minutes. Those ten premature reversals all lost. The other v8 reversals never established the corresponding completed AAC state in v7.

Thus the observed sequence was usually:

```text
acceptance possibility
→ ordinary boundary noise / reclaim
→ v8 labels accepted-auction failure
→ opposite bar entry
→ original unresolved auction resumes
```

not:

```text
completed accepted auction
→ later structural failure
→ new opposite initiative
```

## Why the headline payoff ratio is irrelevant

Average winners were larger than average losers, but only 16.4% of trades won. A 2.09 payoff ratio requires a break-even win rate near 32.4%; the observed rate was roughly half of that. The account lost 86.9% despite exact planned-loss sizing. Lower risk would only hide a strongly negative state definition.

## Research decision

- Do not tighten displacement, rank, session, symbol or flow thresholds around v8.
- Do not rescue it with a cooldown or lower risk.
- Preserve the useful temporal separation: the failure observation bar cannot also own reversal.
- Replace `acceptance_seed` with an explicit untraded `ACCEPTANCE_COMPLETION_OBSERVED` substate.
- Only after that completion may a later deep boundary re-entry record failure, followed by another later initiative.

This interval is development data and cannot be reused as a holdout or success claim. V9 tests the corrected completion-before-failure chronology as one independent state-space revision.
