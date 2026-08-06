# Candidate 09 v11 — targeted market-retest salvage failed

Reproducible implementation-clean NautilusTrader run: GitHub Actions `31116877457`.

## Controlled hypothesis

V11 preserved every immediately executable v10 reversal. Only an otherwise
untradeable accepted-breakout failure was staged for the first failed-boundary
retest/rejection. Entry was then submitted as a market bracket after that
rejection close, while the original accepted excursion remained the stop anchor
and the original v4 equilibrium target remained unchanged.

## Frozen-week result

- baseline pooled daily geometric growth: **+0.680629%**
- pooled NAV multiple: **1.153093x**
- baseline trades: **7**, identical to v10
- week-a: **+8.0446%**, week-b: **0.0000%**, week-c: **+6.7237%**
- `no-retest-salvage`: exactly reproduced v10
- `retest-all`: **+0.139286%/day**, 1.029643x, only one winning trade
- `no-flow`: **+0.124205%/day**

## State-path diagnosis

The salvage path was active but added no trade:

- 215 reversals staged for a failed-boundary retest
- 71 retest/rejection confirmations
- all 71 remained untradeable under the original target and accepted-extreme stop
- 76 failures were reaccepted before an entry
- 68 did not retest within the frozen window

The dominant defect was entry mechanics. A SELL rejection is confirmed only after
price closes farther below the failed high boundary, and a BUY rejection only
after price closes farther above the failed low boundary. Market entry after that
confirmation therefore increased stop distance and consumed target distance.
Some otherwise promising cases also had price risk smaller than the explicit
round-trip composite cost floor.

## Classification

**LOGIC_ERROR_NO_STRUCTURAL_PATH for market-after-retest salvage.**

The retest itself was observable, but using its rejection close as a market entry
was structurally adverse. Waiting for every v10 signal removed almost all
opportunity and weakened growth.

## Valid parts retained

- v10 immediate market signals must remain unchanged
- failed boundary is known causally after the failure bar
- original accepted excursion remains a logical invalidation anchor
- original equilibrium target remains the structural objective
- pending-order timeout and one-order/one-position contract are necessary for a
  passive implementation

V12 submits a native Nautilus GTC limit bracket at the already observed failed
boundary immediately after failure confirmation. The order itself waits for the
retest; no future bar moves entry, stop, target or quantity. `no-limit-salvage`
reproduces v10, `limit-all` tests passive entry for all reversals, and `no-flow`
tests the flow contribution under passive execution.
