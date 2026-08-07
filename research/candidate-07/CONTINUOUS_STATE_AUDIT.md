# Continuous-state audit — why three passing weeks did not validate the system

## Executive verdict

An earlier candidate-07 family passed all three isolated frozen weeks and then
failed catastrophically in the continuous 2024-01-01 to 2026-07-01 replay.
The weekly passes are not valid evidence of live continuity because the strategy
contained market-state memory whose lifetime exceeded each isolated replay's
warm-up. Starting each week from a fresh process removed old failed-thesis locks
which were still active at the same dates in the continuous run.

This is both a **strategy-logic failure** and a **research-protocol failure**.
It is not repaired by adding more random weeks or tuning a timeout.

## Immutable source and execution

- source commit: `1e7bad0f26b49d12bda6559fa5e215d5b5d40721`
- workflow: `candidate-07-nautilus-validation`
- workflow run: `31074423517`
- engine: NautilusTrader `BacktestEngine` 1.230.0
- common config hash: `853afe2c3a081cf670b59630e81b30df40e161031e4f99d8d70221df4525d49a`

The family composed:

1. absorption/reclaim reversals;
2. same-shock failed-absorption continuation;
3. a failed-absorption cascade gate;
4. +3R cost-floor progress protection.

## Results which initially appeared convincing

| Evaluation | Trades | Wins / losses | Win rate | Net return | Daily geometric growth | Profit factor | Max drawdown | Active days |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| isolated W1, 2025-12-22 to 2025-12-29 | 9 | 5 / 4 | 55.56% | +12.2895% | +1.6696% | 2.2287 | 6.1625% | 6 |
| isolated W2, 2025-01-27 to 2025-02-03 | 7 | 4 / 3 | 57.14% | +16.0907% | +2.1543% | 2.6878 | 7.8505% | 5 |
| isolated W3, 2024-06-24 to 2024-07-01 | 7 | 4 / 3 | 57.14% | +7.9109% | +1.0936% | 1.9136 | 8.5711% | 4 |
| continuous 2024-01-01 to 2026-07-01 | 100 | 30 / 70 | 30.00% | **-54.9343%** | **-0.0874%** | **0.6577** | **64.3525%** | 73 |

All three isolated weeks passed the declared gate. The continuous run did not.

## Exact state-path divergence

The cascade gate treated a stopped same-direction absorption as invalid until
price later reached the old scenario's opposing internal-liquidity target. It
had no concept of a superseding dealing range, newly formed source liquidity,
or finite structural episode identity.

The continuous event log contains:

- `2,173` `CAUSAL_ROUTE_READY` events;
- `2,069` `FAILED_ABSORPTION_CASCADE_ACTIVE` rejections;
- only `100` executed trades;
- `60` cascade locks and `58` releases.

Two locks never released before the end of the 2.5-year evaluation:

### Persistent short-reversal lock

- armed: `2024-01-23 17:33:59.999 UTC`
- source scenario: `c07-1706029499999000000-000129`
- direction blocked: `SHORT`
- failed stop: `39,500.4`
- required reset close: `38,925.6`
- later rejected short scenarios attributed to this lock: `1,091`

The strategy therefore carried one January 2024 thesis across later, unrelated
liquidity regimes for the rest of the evaluation.

### Persistent long-reversal lock

- armed: `2025-10-07 14:50:59.999 UTC`
- source scenario: `c07-1759847699999000000-003934`
- direction blocked: `LONG`
- failed stop: `122,627.8`
- required reset close: `124,756.3`
- later rejected long scenarios attributed to this lock: `323`

After this second unresolved lock, both reversal directions were blocked. The
continuous replay consequently produced no trades in the December 2025 W1
period, although a fresh isolated W1 process produced nine trades and passed.

## Why the old market logic was wrong

A stopped absorption proves only that the specific local rejection thesis did
not hold. It does not prove that every later reversal in the same direction is
the same thesis.

The gate's identity was only `direction`. Its reset belonged to one old scenario,
but its blocking scope covered all future scenarios in that direction. This
violated the intended separation between:

- a liquidity pool;
- one contact episode;
- one dealing range;
- a direction label.

A direction is not a market episode. A newly confirmed pool and a newly formed
dealing range can supersede the old failed thesis even when the old target is
never revisited. Conversely, an arbitrary elapsed-time timeout would also be
wrong because it would clear memory without market evidence.

The correct future design, if this family is ever revisited, must bind memory to
a causal structural episode and define supersession from newly completed market
structure. It must not use direction-only lifetime memory or a fitted time
expiry.

## Revised promotion protocol

The following checks are now mandatory before a stateful candidate can be
promoted from a development week:

1. **Archive-history search first.** Before creating a new family, search all
   retained candidate evidence for an equivalent state transition, known
   failure, and stronger predecessor.
2. **Bounded and continuous-context identity check.** Run the selected week in
   both forms:
   - the efficient bounded replay used for iteration;
   - a replay beginning early enough to preserve every state variable which can
     survive the normal warm-up, while scoring only the selected week.
3. **Signal-identity reconciliation.** Compare the scenario IDs or causal source
   identities inside the scored week. A material difference is a state-contract
   failure, not a performance result.
4. **Finite structural ownership.** Every persistent block, rearm condition, or
   consumed-liquidity record must belong to a stated market episode and have a
   causal supersession rule. Direction-only or account-lifetime memory is not
   acceptable.
5. **One uncertainty per experiment.** An experiment must distinguish one of:
   population, state classification, timing, geometry, execution, target, or
   portfolio contention. It must not mix threshold changes across several
   causes.
6. **No outcome-selected repair.** A losing subgroup can motivate a causal
   hypothesis, but the new condition must exist at decision time and describe a
   different market state. It cannot be a statistic chosen merely because it
   removes historical losses.
7. **Portfolio replay before multiplication.** Independent symbol equity curves
   are diagnostic only. A combined candidate is valid only in one
   NautilusTrader engine with one account, current-full-NAV sizing, and one
   portfolio-global pending/open slot.
8. **Long evaluation remains decisive.** Passing several isolated weeks is a
   screening result, never a completion claim.

## Research decision

- The old three-week-pass family remains rejected.
- Its isolated weekly returns must not be cited as evidence of robustness.
- No arbitrary cascade timeout will be added.
- Current research proceeds with the stateless, causally completed multiclock
  first-retest route and an explicit one-engine BTC/XRP portfolio test.
- Any future persistent state must first pass the bounded-versus-continuous
  identity check above.
