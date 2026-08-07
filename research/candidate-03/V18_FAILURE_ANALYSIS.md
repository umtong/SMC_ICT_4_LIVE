# V18 failure analysis — Order-Book Resilience Router

## Candidate intent

V18 attempted to repair V17's false OI-expansion continuation by using official
USD-M `bookTicker` updates to distinguish:

- `L1_VACUUM_CONTINUATION`: high price response, positive microprice, weak
  opposing-queue replenishment, strong depletion, and low opposing depth;
- `L1_ABSORPTION_FULL_RANGE_CHOCH_REVERSAL`: strong directional OFI with poor
  progress, nonpositive microprice, and strong opposing replenishment, followed
  by a full event-range CHoCH;
- mixed response: `NO_TRADE`.

Every threshold was a causal quantile of the ten minutes immediately before the
event. The event itself was excluded from the baseline and the following
30-second observation. Orders, fills, fees, funding, positions, and NAV remained
the native NautilusTrader path.

## Implementation and data checks

The first frozen week `2024-01-08` had official daily bookTicker archives for
all seven required UTC dates. Sparse and full acquisition paths produced the
same causal schedule:

```text
source OI-contraction events             30
OI-expansion L1 pre-candidates           33
novel L1-routed signals                   0
MIXED_RESILIENCE_NO_TRADE                33
retained strong deleveraging signals     14
  FIRST_BREAK_CHOCH_REVERSAL              7
  MEASURED_ACCEPTANCE_CONTINUATION        7
```

The sparse preflight therefore passed the opportunity gate with 14 signals, but
none came from the novel L1 logic.

The frozen second and third weeks cannot be evaluated under the same data
contract:

```text
2025-06-23 through 2025-06-27 official daily bookTicker archives: HTTP 404
2022-05-16 through 2022-05-22 official daily bookTicker archives: HTTP 404
```

The acquisition planner confirmed that the missing files are the exact UTC
dates required by the V18 pre-candidates. This is not a strategy PnL result and
not a NautilusTrader failure. It is a historical-data availability failure.

## Why V18 is not a valid project candidate

A candidate whose defining state variable is observable only in one frozen
week cannot be tested for week-to-week generalization, cannot enter the required
long evaluation, and cannot support future cross-symbol transfer on a
consistent historical contract.

Running only the 2024 week would create precisely the prohibited form of
period-specific evidence. Substituting a different L1 source only for the
missing weeks would also break the controlled-data contract.

V18 is therefore abandoned as a complete candidate **before performance
interpretation**.

## Largest limiting factors

1. **Historical best-quote coverage was not invariant across the frozen weeks.**
   This is the decisive failure.
2. **The novel hard-conjunction router emitted zero L1 states in the one
   available week.** The first-week schedule was economically identical to the
   previously retained strong V13 states.
3. **The 33 mixed responses show that queue resilience is not naturally a set
   of independent binary clauses.** Price response, replenishment, depletion,
   depth, and microprice are correlated manifestations of fewer latent axes.

## Valid components preserved

- Pre-shock and post-shock windows were causally separated.
- Candidate-local thresholds avoided global return-fitted parameter search.
- OFI, price response, microprice, and opposing liquidity recovery remain
  conceptually useful.
- The sparse acquisition planner proved that detector-preserving data reduction
  can be verified by identical signal hashes and state counts.
- `MIXED_RESILIENCE_NO_TRADE` correctly prevented forced classification.

## Required next hypothesis

V19 must preserve the microstructure question while using an invariant official
historical source across all frozen weeks. Futures and spot `aggTrades` satisfy
that requirement.

V19 will estimate event-time executed-flow resilience from futures and spot
aggregate trades:

```text
pre-event 30-second block distribution
  -> post-event price progress per gross notional
  -> futures/spot signed-flow agreement
  -> opposite-trade share and price retracement
  -> continuation, absorption-pending, or no trade
```

The design will use a small number of coherent axes rather than V18's seven- and
six-condition conjunctions. Absorption still requires a later full event-range
CHoCH before reversal. NautilusTrader remains the sole execution and accounting
engine.
