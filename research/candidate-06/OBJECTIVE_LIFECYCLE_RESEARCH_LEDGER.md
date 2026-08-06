# Candidate 07 — Objective-Lifecycle Acceptance Relay (OLAR)

## Status before market evaluation

This document freezes the hypothesis, diagnostics, implementation boundaries,
and decision rule **before** the newly selected BTC first week is evaluated.
No OLAR return, trade, win, loss, or gate result was visible when these rules
were written.

The candidate reuses the project's existing checksum-verified one-minute data,
causal completed-bar clock, NautilusTrader 1.230.0 execution path, fixed
three-percent whole-NAV planned-loss budget, fee/slippage/fill assumptions,
single global entry/position slot, and NAV accounting. It does not introduce a
new backtest engine.

## Lessons inherited from failed candidates

### Implementation failures are not alpha failures

The repaired ADOM campaign remains blocked before market evaluation by an
unassigned GitHub Actions runner. A missing runner, registration failure,
syntax error, native-order contract failure, timestamp error, or absent summary
is an implementation/infrastructure result. None can be interpreted as support
for or against a price-delivery hypothesis.

### HML/HSP exposed a lifecycle defect, not merely a bad direction filter

The leading HML/HSP first week passed the declared growth/trade/win-rate gate,
but sealed weeks failed. Reinspection of preserved trades and scenario events
showed that:

1. one structurally surviving HTF bias could generate entries for many hours;
2. the same confirmed target prices could be selected repeatedly because target
   objectives had no consumed/reserved state;
3. the HTF `bias.extreme` was continuously updated, so a rolling observation
   could be treated as a new target without a separate objective-formation
   event;
4. several stopped trades first moved materially in the expected direction,
   then reversed without a completed-bar scenario invalidation; and
5. failure occurred on both old and young contexts, so a fitted minute cutoff or
   blanket SHORT ban would not be a structural repair.

The parent detector's useful part is retained: completed HTF acceptance,
causally confirmed lower-timeframe swing/equal liquidity, a counter-direction
sweep, and a separate response. The failed open-ended target/context contracts
are replaced.

### AFHR/SIAR/SIPR ruled out easy quality stacking

Prior-range/volume quality, fixed freshness clocks, flow-surprise filters, and
consecutive HTF acceptance either collapsed opportunity count or failed sealed
weeks. OLAR therefore does not stack another score or search another freshness
threshold. It changes the market-state lifecycle: what objective still exists,
which directional leg is current, and what completed event invalidates it.

## Research basis and limits

### Market microstructure

The design is informed by primary work showing that short-horizon price changes
are more directly related to order-flow imbalance and available depth than to
trade volume alone, and that impact can partially reverse as liquidity
replenishes. Relevant references include:

- Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*;
- Bechler & Ludkovski, *Order Flows and Limit Order Book Resiliency on the
  Meso-Scale*;
- Roşu, *A Dynamic Model of the Limit Order Book*; and
- Degryse et al., work on aggressive orders and limit-order-book resiliency.

Candidate data is one-minute OHLCV with taker-buy volume, not full message-level
order additions/cancellations/depth. OLAR therefore does **not** claim to measure
true order-flow imbalance or book resiliency. The literature motivates the
state sequence — effective acceptance, continuation, failure, and recovery —
while all implemented variables remain observable from the declared data.

### ICT/SMC terminology

ICT 2022 mentorship material is used to preserve the intended sequence behind
internal/external liquidity, market structure shift, displacement, retracement,
and objective delivery. Those videos define trading terminology and chronology;
they are not treated as scientific validation. OLAR requires the sequence to be
expressed as causal completed-bar states rather than discretionary chart labels.

## Frozen OLAR hypothesis

A long or short continuation is tradable only when all of the following exist in
order:

1. **HTF context** — a completed 60-minute auction accepts price beyond a prior
   multi-auction range under the unchanged HML price/range/body/volume/bias-flow
   contract.
2. **Current directional leg** — the HTF acceptance creates the initial leg. A
   later completed 15-minute auction can replace it only by independently
   accepting price in the same direction under the same acceptance definition.
3. **Internal liquidity event** — a causally confirmed 5-minute swing or equal
   pool is swept against the current leg, using the unchanged HML sweep rules.
4. **Separate response** — a later completed one-minute bar breaks the sweep bar
   in the HTF direction under the unchanged response-body/location/flow rules.
5. **Unresolved objective** — the nearest structurally valid target is either an
   untouched confirmed opposite-side 5-minute pool or the fixed extreme of the
   current accepted leg. Rolling fast/slow extrema and synthetic ATR extensions
   are not objectives.
6. **One-use reservation** — the chosen objective is reserved by the signal and
   cannot be selected by a later leg. An objective touched after confirmation is
   consumed even when no trade was taken.
7. **One signal per leg** — after one response reserves the current leg, another
   entry requires a newly completed same-direction 15-minute acceptance.

## Invalidation and live-position behavior

The lifecycle is event-based rather than time-based:

- a completed opposing 15-minute acceptance invalidates the HTF context;
- a completed opposing control bar through the current leg origin suspends the
  directional leg while leaving the broader HTF boundary available for a future
  independent renewal;
- HTF accepted-boundary loss or HTF context replacement invalidates the old
  context; and
- any matching open OLAR position is flattened through the existing Nautilus
  order/position path when one of those structural events occurs.

A one-bar-delayed signal is revalidated immediately before order construction.
If its HTF context or reserved leg was superseded, it abstains rather than
entering stale information.

## Variables intentionally unchanged

- 60-minute HTF acceptance detector;
- 5-minute confirmed swing/equal liquidity detector;
- lower-timeframe sweep depth, reclaim, impulse-position, and response rules;
- structural sweep stop plus the existing HTF ATR buffer;
- minimum structural reward/risk and delayed-entry net reward/risk checks;
- maximum 60-minute holding fallback;
- whole-account NAV risk sizing at 3% planned loss;
- fees, one-tick slippage, probabilistic touch fill, and fill seed;
- BTC first, then two sealed BTC weeks;
- performance gates; and
- one pending new entry or one position globally.

The 15-minute control auction is a new state layer, not a grid-search dimension.
It divides the 60-minute context into four completed auctions and reuses the HTF
acceptance thresholds. No control-period or threshold sweep is authorized.

## Fresh deterministic evaluation weeks

Previously exposed candidate-06 weeks are excluded from candidate-07 selection:

- 2024-02-26
- 2024-09-23
- 2024-04-22

Selection contract:

```text
sha256(candidate-07|BTCUSDT|objective-lifecycle-v1)
first 8 bytes -> unsigned PRNG seed 420067234334513947
sample without replacement from eligible Mondays
```

Frozen order:

1. first week: **2024-01-29**
2. sealed week 2: **2024-12-09**
3. sealed week 3: **2024-05-13**

The old HML weeks remain available for failure attribution only and cannot be
used to select or modify OLAR.

## Predeclared diagnostic controls

| Variant | Selection eligible | Purpose |
|---|---:|---|
| `olar_full` | yes | Complete objective, leg, invalidation, and structural-exit lifecycle |
| `olar_objective_reuse_ablation` | no | Same control/entry/exit logic, but target pools may be reused across later legs |
| `hml_parent_reference` | no | Unchanged failed parent on the same new week |

The ablation and parent cannot replace a failed full OLAR result. Their role is
to determine whether one-use objectives improve behavior and whether any result
comes from the new lifecycle rather than a favorable week for the parent.

## Decision rule

1. Registration, syntax, causal-clock, unit, integration, Nautilus version,
   native execution, data, or missing-summary failures are implementation
   failures. Repair the implementation and rerun the same frozen first week.
2. Once those checks pass, failure of `olar_full` on the first-week gate is a
   logical failure. Do not select the ablation, tune the control period, change
   thresholds, remove a direction, reduce costs, or alter risk to rescue it.
3. Only a passing full first week unlocks the two sealed weeks with the exact
   locked configuration.
4. Failure of either sealed week is a generalization failure. Record the causal
   decomposition and discard or reformulate the hypothesis without tuning on the
   sealed data.
5. Only all three passing weeks authorize long evaluation. Long evaluation does
   not authorize performance claims until its own costs, drawdown, opportunity
   distribution, and concentration diagnostics pass.

## Required diagnostics

For every run, preserve at least:

- objective confirmation, touch, reservation, and invalidation counts;
- entries and abstentions by HTF context and directional leg;
- same-direction renewals, leg-origin losses, and opposing control acceptances;
- structural exits versus stop, target, timeout, and other exits;
- target reason and repeat-use attribution;
- age of context and leg at signal, without using age as a filter;
- direction, session, day, and scenario concentration;
- MFE/MAE and whether failed trades first delivered in the expected direction;
- cost and fill diagnostics; and
- all declared NAV/gate metrics.

## Research-process improvement carried forward

The candidate changes one structural claim instead of appending conditions to a
winning week. Pattern detection is inherited; scenario lifecycle is new;
execution and accounting are unchanged. This separation makes the next result
interpretable:

- a registration/runtime problem is implementation;
- no or poor trades with correct state transitions is logic;
- improvement only in the reuse ablation is evidence against one-use objectives;
- improvement only in the full version with lower repeat-target and structural
  reversal losses is evidence for the lifecycle claim; and
- failure on sealed weeks is not repaired with sealed-week-specific rules.
