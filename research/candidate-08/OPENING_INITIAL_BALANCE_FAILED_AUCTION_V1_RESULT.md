# Opening Initial-Balance Failed Auction V1 — terminal result

## Decision

`SESSION_OPENING_INITIAL_BALANCE_FAILED_AUCTION` is rejected after an implementation-clean first BTC
week produced no tradeable signals.

## Frozen first-week funnel

For 2024-04-08 through 2024-04-15 UTC, the causal detector found:

- 66 complete thirty-minute initial balances;
- 57 first edge sweeps that closed back inside;
- 35 attempts that subsequently reaccepted price beyond the swept edge;
- 7 attempts whose opposite IB edge was already consumed before entry;
- 4 otherwise-confirmed attempts whose opposite-edge objective could not pay realistic costs;
- 0 final signals and 0 trades.

All execution, planned-loss, fill-adjusted-loss, realized-loss, funding, liquidation, causality and
residual-exposure contracts passed. This is therefore a clean scenario-logic failure, not a runner or
risk-sizing failure.

## Interpretation

A five-minute sweep and close back inside the opening balance was too early to identify a durable
failed auction. Most attempts returned to outside-price acceptance before a separate displacement
could produce an executable, cost-after path to the opposite IB edge. The result does not justify
weakening excursion, displacement, cost or target-consumption conditions.

## Next independent state

The dominant observed transition was reacceptance outside the initial balance. The next candidate
therefore tests the economically opposite state:

```text
completed 30-minute initial balance
→ displaced M5 close outside
→ immediate second completed outside close
→ later separate accepted-boundary retest
→ one completed-IB-width extension
```

This is `SESSION_OPENING_DRIVE_ACCEPTANCE_CONTINUATION`, not an ablation or relaxed failed-auction
reversal.
