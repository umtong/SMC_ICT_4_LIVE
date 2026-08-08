# Candidate 10 — Autonomous round 5

Round 5 is the final autonomous research round.  It does not promote a controlled
week, a zero-trade holdout, or a loss-reduction overlay as project success.

## Frozen execution contract

All final lineages use:

- frozen Candidate 11 source commit `f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327`;
- NautilusTrader 1.230.0 for clocks, orders, fills, fees, margin, positions and
  account NAV;
- current all-cost account NAV as the next trade's risk base;
- maximum modeled all-cost loss of 3% of that NAV;
- size-dependent impact solved inside position sizing and debited at real fills;
- one global pending-entry/position slot across BTC, ETH, SOL and XRP;
- no future observation, PnL-labelled feature, symbol whitelist or risk
  multiplier.

## v49 — cross-market transfer-state FAR

v47's event-direction rank-one observation was decomposed into two causal states:

- `DISTRIBUTED_TRANSFER`: peers already moved in the proposed direction, so the
  candidate must contribute the frozen existing Candidate 11 local confirmation
  impulse;
- `PIONEER_TRANSFER`: peers have not moved, so the candidate may lead only when
  the FAR direction reverses its own prior directional auction rather than
  extending it.

The first workflow exposed an implementation error: the v28 leadership adapter
did not forward the frozen impulse threshold.  Signal, period, entry, target,
stop, costs, risk and state contracts were unchanged.  Adapter access was fixed
and the exact same controls and continuous period were rerun.  The repaired
lineage passed 112 regression tests before Nautilus execution.

## v50 — independent external-draw FAR

This is not another source-equilibrium filter.  It leaves that lineage and keeps
Candidate 11's original external target, with the v29 requirement that the draw
is a pre-existing `EXTERNAL_HAZARD_DOMINANCE` liquidity pool.  The exact
ablation compares all otherwise-approved external-draw FAR plans with plans whose
candidate is event-direction rank one among synchronized BTC, ETH, SOL and XRP.

The study runs controlled attribution periods, six untouched 2026 calendar
months and one continuous 2026-H1 account.  The monthly jobs diagnose occurrence
without replacing the continuous account as the success evidence.

## Automatic final verdict

`.github/workflows/candidate-10-finalize.yml` collects the latest completed v49
and v50 artifacts and writes:

- `FINAL_RESEARCH_RESULT.md`;
- `final_research_metrics.json`.

The complete success contract is fixed before collection:

- at least 90 continuous calendar days;
- at least 30 closed trades;
- cost-after win rate at least 90%;
- cost-after payoff ratio at least 1.20;
- cost-after geometric daily growth at least 1%;
- maximum drawdown at most 20%;
- zero implementation errors, liquidation detections and global-slot overlaps.

No result may be called complete unless every condition is satisfied by a frozen
continuous artifact.

## Known failure conditions

The final source-equilibrium family must be rejected when it remains too sparse,
fails the continuous cost-after growth contract, or derives its apparent quality
only from controlled weeks.  The external-draw family must be rejected when its
independent target does not improve continuous occurrence and expectation after
cost.  Event-rank filtering is a negative selector, not evidence of alpha, when
it merely removes all trades.

The machine-generated final files are the authoritative numerical verdict.
