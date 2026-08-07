# Candidate 08 — Research Learning Ledger

This ledger records what changed in the **way the research is conducted**, not only which strategy
won or lost.  It is deliberately separate from performance evidence.  A result is not promoted
unless the corresponding NautilusTrader run, data contract, scenario attribution, shared-account
risk contract, and post-run path evidence are all complete.

## Permanent decision hierarchy

1. **Infrastructure failure** — runner, workflow, archive, checksum, environment, or network did not
   produce an evaluable run.  Never count it as a strategy loss.
2. **Implementation failure** — code did not execute the frozen market hypothesis exactly.  Change
   only the defective implementation variable and rerun the same fixed week.
3. **Evidence-contract failure** — execution occurred, but causality, family attribution, exact
   cadence, risk accounting, or full-horizon path evidence is incomplete.  Do not infer economic
   failure or success.
4. **Logic failure** — the exact frozen hypothesis ran correctly under realistic cost and account
   contracts and failed economically.  Permit only the predeclared ablation, then discard or rebuild
   a structurally new base.
5. **Promotion evidence** — all predeclared windows and concentration, opportunity, risk, execution,
   and cost-after geometric-growth gates pass.  Diagnostic ablations can never be promoted directly.

## Research sequence and durable lessons

### 1. Three-bar swing liquidity

**Failure:** causal three-bar pivots were correctly implemented but were mostly local one-minute
rotation, not economically persistent external liquidity.

**Dominant factor:** the market concept was wrong, not the timestamp implementation.

**Retained:** causal pivot confirmation, scenario event logging, shared-NAV sizing, bracket exits,
checksum manifests.

**Research improvement:** distinguish *future-free pattern detection* from *valid economic state*.
A causal detector can still detect the wrong object perfectly.

### 2. Time-boundary and session effects

**Failure:** quarter-hour, hour, funding, and session boundaries changed activity but did not provide
stable direction by themselves.

**Retained:** time is a prior for event intensity, liquidity, and execution quality; it is not an
entry direction.

**Research improvement:** contextual variables cannot be promoted into directional triggers merely
because event rates increase around them.

### 3. Completed-range liquidity and FVG entry

**Failure:** completed 4-hour/day/week levels improved the liquidity definition, but immediate FVG
midpoint entry frequently occurred before repricing resumed.

**Retained:** completed external liquidity inventory and nearest active completed external target.
No fixed-R fallback.

**Research improvement:** separate the pattern detector (FVG or low-overlap corridor) from the
scenario confirmation.  A retracement zone is not evidence that the auction has restarted.

### 4. Five-minute reacceleration and one-minute MSS

**Failure:** five-minute confirmation removed bad entries but was too late for cost-after target
geometry; one-minute MSS restored timing but failed across weeks and assets in a common regime.

**Retained:** explicit later confirmation and market-wide episode grouping.

**Research improvement:** when all assets fail together, do not fit asset-specific parameters.  Test
whether the market-state interpretation itself is wrong.

### 5. Ten-second aggregate-trade acceptance

**Failure:** retest contraction was not a necessary condition for valid initiative continuation.  A
single diagnostic ablation improved one week but the gain was concentrated in one trade and did not
meet the project goal.

**Retained:** executed aggressive-flow data, causal activity baselines, completed external targets,
and the verified native shared-account Nautilus adapter.

**Research improvement:** a positive ablation is evidence about the removed mechanism, not a license
to promote the ablated variant.  Rebuild a new base from the economic lesson.

### 6. Auction router

**Failure under review:** simple breakout/retest/reacceleration can confuse initiative acceptance
with exhaustion or failed auction.

**Implementation lessons:**

- failed-auction stop must include the full sweep observed through confirmation;
- family attribution must cover every signal and closed trade;
- one family can be removed only when both families traded independently and one is positive while
  the other is negative;
- workflow queue or shell failure is not market evidence.

### 7. Flow–price response V3

**Hypothesis:** unusual aggressive flow is useful only relative to the price response it produces.
Classify completed external-liquidity interactions into retained initiative response or absorbed
response, then require a separate opposite response for reversal.

**Implementation corrections made before economic evidence:**

- interaction and absorption buckets are excluded from their subsequent response windows;
- expected and realized response use matching additive noise-normalized units;
- each completed bucket uses the impact coefficient observable before that bucket;
- every response window must contain exact consecutive ten-second timestamps;
- path diagnostics must cover the complete maximum-hold horizon with no internal gaps;
- stop and target first-hit timestamps are recorded independently;
- zero-trade evidence explicitly records the current path-diagnostic revision;
- the V3 workflow calls the V3 detector, V2 exact-cadence orchestrator, and evidence-only V4 wrapper.

**Current status:** fixed BTC screen-01 NautilusTrader replay is the next authoritative economic
result.  No performance claim is made until its committed `stage_decision.json` and suite evidence
exist.

## Current anti-overfitting rules

- No parameter search over the three fixed weeks.
- No threshold is changed after observing a trade result.
- BTC first; screen-02 and screen-03 only after the first-week gate.
- ETH/SOL/XRP only after all BTC screen weeks pass unchanged.
- Long evaluation only after the three-week gate.
- One economic-family ablation at most, under a frozen selector.
- A diagnostic result is never directly promotable.
- Preserve continuous path facts rather than converting every observation to a fitted boolean.
- Do not infer passive absorption from aggregate trades; require order-book data for that claim.

## Success and failure reporting requirements

Every discarded candidate must record:

- exact implementation revision and data contract;
- whether the failure was infrastructure, implementation, evidence, or logic;
- dominant loss or opportunity-suppression mechanism;
- scenario-family and market-state contribution;
- whether direction, timing, target, or invalidation failed;
- any component that worked and why it is retained;
- the one structural lesson transferred to the next hypothesis.

Every promoted candidate must additionally demonstrate:

- multiple independent wins and enough opportunities per fixed week;
- no single trade, day, week, or asset concentration violation;
- realistic cost-after shared-account NAV growth;
- no liquidation, unexpected close, residual exposure, or risk-budget violation;
- unchanged logic across the predeclared validation environments.
