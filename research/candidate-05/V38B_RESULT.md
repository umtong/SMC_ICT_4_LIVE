# Candidate 05 v38b result — measured target reachability

## Decision

**Discard v38b and the isolated-SMT reversal family.** The one permitted
reachability ablation passed every implementation and shared-account integrity
contract but generated no incremental trade in the untouched deterministic
out-of-sample week. The fixed one-third measured-move requirement avoided two
net-losing v38 trades, but it did not create positive independent expectancy.
No alternative multiple will be tested.

Authoritative workflow: GitHub Actions run `31160186178`, source commit
`a7a8fac18127a76d1377fd033f92ca2bd8da2560`, artifact `8986923022`.

## Frozen untouched week

Selection was made before replay by:

```text
sha256("candidate-05-v38b-reachability-oos-v1")
modulo every eligible seven-day start from 2024-01-01 through 2025-12-25
= 2025-12-04 through 2025-12-10
```

Build range: `2025-12-02` through `2025-12-12`.

| Variant | Total return | Trades / wins | Incremental family |
|---|---:|---:|---:|
| exact v26 control | -6.9873% | 6 / 2 | none |
| unrestricted v38 | -7.3507% | 8 / 3 | 2 / 1, -475.12 USDT |
| v38b reachability | -6.9873% | 6 / 2 | 0 / 0 |

All three runs used one NautilusTrader shared account for BTCUSDT, ETHUSDT,
SOLUSDT and XRPUSDT. All had exact 3% current-NAV loss budgets, no liquidation,
no order rejection or denial, no unresolved order or position, and no global
entry-slot violation.

## Implementation versus logic

The implementation was valid:

- `smc4 doctor`, compilation and all unit/contract tests passed;
- all four instruments replayed positive bar counts;
- scenario records matched NautilusTrader positions and PnL;
- same-timestamp peer observations remained excluded;
- v38b evaluated exactly two completed v38 CHoCH states and rejected both by the
  predeclared target-reachability rule;
- it did not change target identity, stop, quantity, fees, slippage or execution.

The failure is logical and statistical:

- unrestricted isolated reversal again had negative incremental expectancy;
- the reachability predicate eliminated that loss but produced no trade;
- the family therefore has neither robust directional accuracy nor sufficient
  opportunity density after its necessary causal checks.

The clean in-sample split between reachable winners and unreachable losers was
not accepted as an alpha discovery because the first untouched week offered no
positive trade under the frozen rule.

## Retained observations

1. Two non-confirming peers are insufficient; even unanimous session
   non-confirmation does not prove transitory local price pressure.
2. A one-minute peer-continuation veto cannot fully distinguish common price
   discovery from local liquidity shock.
3. Local reclaim and CHoCH can be a temporary counter-rotation inside a
   persistent original-direction auction.
4. A credible opposing-liquidity target must be evaluated as part of scenario
   validity, but filtering distant targets alone can collapse opportunity
   density rather than discover alpha.
5. The next family must wait for the reversal thesis itself to fail and then
   trade the demonstrated state transition, rather than predict reversal from
   the raid.

## Next research family

The next hypothesis is **failed-reversal continuation**:

```text
completed-session liquidity raid
-> unanimous peer non-confirmation and local reclaim / CHoCH attempt
-> reversal structural invalidation beyond the original sweep extreme
-> original raid direction reaccepted with efficient aggressive flow and
   threatened-side depth withdrawal
-> first later defended retest from outside
-> continuation toward the next still-live same-direction liquidity pool
```

No strategy is promoted from this outline alone. The frozen v38 trade cases are
first examined observationally after reversal failure to determine whether the
reacceptance-and-retest sequence occurs with enough frequency and directional
follow-through to justify implementation.
