# Candidate 06 AIMD Research Ledger

## Hypothesis

A completed auction can represent genuine price discovery when volume-at-price
value migrates away from the preceding auction, its close accepts the migrated
side, realized movement is efficient, and aggregate-trade delta is aligned.
The first opposing-flow pullback is tradable only if it cannot re-enter old
value.  A separate completed minute must then resume in the discovery direction.
The first objective is the already completed migration-auction extreme; if that
has been consumed, a projection based on the larger of POC migration and half
the current value width is used.

## Causal state order

```text
COMPLETED PROFILE A
→ COMPLETED PROFILE B MIGRATES POC AND VALUE
→ B CLOSES OUTSIDE A VALUE WITH EFFICIENT ALIGNED DELTA
→ MIGRATION ACTIVE FOR ONE PROFILE ONLY
→ FIRST OPPOSING-FLOW RETEST TOUCHES MIGRATED EDGE
→ RETEST CLOSE CANNOT REENTER OLD VALUE
→ SEPARATE COMPLETED MINUTE BREAKS RETEST STRUCTURE WITH ALIGNED FLOW
→ ENTRY
→ MIGRATION EXTREME / MIGRATED-VALUE EXTENSION
```

The profile closing at the current timestamp is ingested only after the current
minute has been processed.  It cannot create and retest its own context.

## Fixed first-week experiment

- instrument: BTCUSDT perpetual
- first frozen week: 2024-02-26 UTC
- profile clock: fixed 15 minutes
- value area: 70 percent of aggregate-trade volume
- execution and accounting: NautilusTrader 1.230.0 only
- planned loss: 3 percent of current whole-account NAV
- costs, slippage, fill model and global one-slot contract unchanged

## Fixed variants

1. `aimd_value_migration_full`: POC migration, value migration, directional
   efficiency, aligned profile delta, opposing-flow retest and separate response.
2. `aimd_without_delta_ablation`: remove only profile delta alignment.
3. `aimd_without_poc_migration_ablation`: remove only POC displacement.
4. `aimd_kline_flow_reference`: replace only minute aggregate-trade flow with
   the existing kline taker-flow proxy.

Only the full variant is eligible.  Variant order is not a return ranking.

## Implementation versus logic decision

A checksum, timestamp, profile count, minute alignment, constructor, Nautilus
API, output or causality failure is an implementation/data failure.  Only that
defect may be repaired before rerunning the identical week.  Once all four
variants produce valid Nautilus metrics, failure of the full first-week gate is
a logic failure.  Threshold, period, stop, target, cost, risk or week rescue is
forbidden.

## Promotion

The unchanged full variant opens the two sealed BTC weeks only after every
first-week gate passes.  Long evaluation is authorized only when all three weeks
pass without configuration changes.
