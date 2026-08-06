# ADOM Implementation Audit Before First Market Evaluation

## Scope

This audit was completed before any ADOM market-performance output was observed.
It separates implementation and validation defects from defects in the
accepted-auction defense-origin hypothesis.  The frozen first BTC week remains
the next evaluation.  No result-driven parameter, scenario, risk, cost, or fill
change is permitted by this repair.

## Implementation defects corrected

### 1. Completed-bar auction-boundary clock

Candidate market data stamps a one-minute source interval at its completed-bar
observation time (`source open_time + 1 minute`).  The fixed-auction engine maps
that observation back to its source interval before assigning a 30-minute
bucket.  ADOM initially calculated order expiry directly from the observation
timestamp, so a defense bar observed exactly at an auction boundary was assigned
to the following auction and could remain working for an extra 30 minutes.

Classification: **implementation error**.

Correction: calculate auction membership from
`decision_ts_ns - ONE_MINUTE_NS`.  A defense bar observed exactly at the auction
end now has zero causal lifetime and is rejected with
`DEFENSE_ORIGIN_ENTRY_HAS_NO_CAUSAL_LIFETIME`.  Regression tests cover an
ordinary in-auction bar, the exact boundary, and the first bar of the next
auction.

### 2. Self-invalidating workflow assertion

The workflow rejected any `_attempt_entry` source containing the text
`DEFENSE_ORIGIN_LIMIT`.  The registered implementation legitimately includes
that text in an event reason, so a correct implementation would fail before the
NautilusTrader evaluation began.

Classification: **implementation-validation error**.

Correction: verify the intended architectural contract instead.  The execution
method must call the pure `resolve_entry_placement` detector and contain native
`OrderType.LIMIT`/`TimeInForce.GTD` construction, while it must not read the
`sac_entry_execution` configuration directly.  The pure detector remains the
only component selecting market versus defense-origin placement.

### 3. Partial-fill single-slot contract

A passive parent limit can partially fill while its remainder stays open.  This
would leave one position plus one working new-entry remainder, violating the
project-wide rule that pending new entries and open positions sum to at most
one.  Protective stop and target orders are exits and are not counted as new
entries.

Classification: **execution lifecycle contract gap**.

A first repair draft attempted to cancel only the residual parent order.  That
draft was rejected before commit.  NautilusTrader's native bracket parent is an
OTO order, and generic parent cancellation cascades to its linked stop and
target.  The draft therefore could have left the partially filled position
without native protection.

Final correction: after `PositionOpened`, inspect the native cache for the exact
parent `entry_client_order_id`.  If that parent is still open, classify the
trade as `PARTIAL_ENTRY_SINGLE_SLOT_ABORT`, cancel all remaining orders, and
flatten the native position.  A later position-size change, parent-cancel
rejection, or parent terminal callback is handled without resetting the active
POSITION record; bounded cancellation retry and repeat flatten requests cover a
late residual fill.  This is deliberately conservative and is not counted as an
alpha win or loss caused by the scenario thesis.

### 4. Explicit partial OTO trigger mode

NautilusTrader 1.230.0 currently defaults the backtest venue to partial OTO
triggering.  Relying on an implicit default would make the protection assumption
fragile across environment changes.

Classification: **execution configuration ambiguity**.

Correction: `nautilus_runner.py` explicitly sets
`oto_trigger_mode=OtoTriggerMode.PARTIAL`.  A protective child can therefore be
released on the first partial fill while the strategy initiates its fail-safe
abort.  The object-level contract test also verifies the OTO parent, OUO
reduce-only children, GTD entry, and GTC stop/target relationships.

## Native order-object contract

The prebuilt NautilusTrader 1.230.0 environment must construct the ADOM bracket
with the following properties before any market campaign runs:

- parent entry: `LIMIT`, `GTD`, post-only, non-reduce-only, fixed expiry, OTO;
- stop child: `STOP_MARKET`, `GTC`, reduce-only, linked to the parent, OUO;
- target child: `LIMIT`, `GTC`, post-only, reduce-only, linked to the parent, OUO;
- simulated venue: contingent orders enabled, reduce-only enabled, partial OTO
  triggering explicitly selected.

`test_adom_native_order_contract.py` asserts these properties against actual
Nautilus order objects rather than source-text approximations.

## Logical hypothesis held fixed

None of the following was changed by this audit:

- immediately preceding completed 30-minute auction as the liquidity source;
- completed boundary sweep, outside acceptance, and first held retest;
- a separate completed one-minute directional-defense bar;
- passive entry at that defense bar's open;
- expiry at the end of the setup's current fixed auction;
- the same structural stop and projection target;
- whole-account NAV sizing with a three-percent planned loss budget;
- the same fee, slippage, fill probability, data, frozen weeks, and gates;
- the market-close reference variant and the predeclared fixed selection order.

## Decision rule after repair

The repaired first-week campaign is a controlled repeat because the preceding
blockers were implementation errors and no ADOM performance was observed.  A
runtime, causality, native-order, lifecycle, deterministic reference-regression,
or data-contract failure remains an implementation failure and must be fixed
before interpreting market results.

Once those checks pass, failure of the full ADOM variant to satisfy the frozen
first-week gates is a **logical failure of this candidate at the declared
contract**.  It must be recorded and discarded without tuning the auction
period, defense-origin price, expiry, stop, target, session, risk fraction,
costs, or fill model to rescue the result.

## NautilusTrader source contracts checked

The audit was cross-checked against NautilusTrader 1.230.0 source for:

- `nautilus_trader/common/factories.pyx` bracket parent/child construction;
- `crates/backtest/src/python/engine.rs` partial OTO venue configuration;
- `crates/execution/src/matching_engine/engine.rs` OTO child activation,
  contingent cancellation, GTD expiry, and partial-fill processing.
