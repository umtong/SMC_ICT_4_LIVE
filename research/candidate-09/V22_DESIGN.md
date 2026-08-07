# Candidate-09 v22 — OI position-reduction exhaustion

## Market question

Can an abnormal completed five-minute reduction in base-asset open interest distinguish
forced position reduction from new-position price discovery well enough to make a
cost-after reversal after price progress stalls and the pre-shock auction edge is
reclaimed?

## Causal sequence

1. Use only a completed Binance UM metrics snapshot, exposed one full minute after
   `create_time`.
2. Compare its OI change with the previous 24 completed five-minute changes. A baseline
   pulse requires a drop larger than both 5 bp and twice the prior median absolute change.
3. The same completed five-minute window must displace at least 0.5 current ATR, close
   beyond the preceding 15-minute auction edge, exceed median participation, and have
   aligned metrics taker flow.
4. Do not enter. Require one-minute aggression in the pulse direction to stop producing
   price progress.
5. Require opposite displacement and one-minute flow to close back inside the frozen
   pre-shock edge.
6. Enter toward the frozen pre-shock volume-weighted equilibrium. Invalidate beyond all
   pulse extremes. Apply the unchanged full-cost 1.2R gate and 3% NAV loss budget.

## Exact controls

- `no-oi`: same price/flow event without the OI condition.
- `oi-rise`: identical magnitude rule with an OI increase, testing new-position expansion.
- `no-stall`: retain the OI drop but remove only the failed-price-progress state.

No parameter search, target fitting, risk scaling, or evaluation-period change is used.
