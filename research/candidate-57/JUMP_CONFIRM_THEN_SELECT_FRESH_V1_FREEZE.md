# Candidate 57 — conditional untouched test of post-confirmation jump arbitration

## Structural hypothesis

The source jump implementation resolves simultaneous symbols at the completed
four-hour boundary and then waits for the selected symbol to confirm.  That can
discard the symbol whose post-cascade auction actually transitions.  The
`confirm_then_select` adapter instead retains all already-qualified simultaneous
symbols as non-order candidates, applies the same frozen two-bar price
confirmation to each, and submits only one candidate after confirmation.

## Development promotion rule

The consumed 2026-07-29 through 2026-08-09 comparison is used only to decide
whether the new routing policy deserves an untouched test.  Untouched data is
not downloaded unless all of the following are true:

- both `selected_then_confirm` and `confirm_then_select` produced valid one-slot
  accounts;
- `confirm_then_select` completed at least three trades;
- its cost-after geometric daily growth is positive;
- its profit factor is greater than one, or it has wins and no losses;
- its geometric daily growth and total return are both greater than the source
  `selected_then_confirm` control.

## Untouched interval

If promoted, run both policies on **2025-03-03 through 2025-03-16 UTC**. Binance
metrics begin 2025-02-27 for strict as-of peer state. This interval was not used
to design the candidate-pool policy, two-bar confirmation or promotion rule.

## Frozen contract

- completed four-hour source jump with absolute prior-only z-score at least 2;
- 18 prior completed four-hour returns;
- both reversal directions;
- peer-taker conditional scoring:
  - at least three of four peers aligned -> source max absolute z;
  - otherwise -> least absolute qualified z;
- wait at least two completed five-minute bars;
- confirm by a close through the terminal jump-candle extreme in the reversal
  direction;
- candidate expiry after 15 completed minutes;
- post-jump extension included in structural invalidation;
- original source-event 240-minute clock not restarted;
- transient +0.4R arm and +1.0R escape management;
- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
- candidate-pool members are not orders;
- one global submitted entry or open position;
- current-NAV 3% planned-loss sizing;
- project costs, funding safety and NautilusTrader matching.

## Promotion after untouched

The new policy is retained only if it remains positive after costs, has at least
seven completed trades, profit factor above one, no account violation, drawdown
no greater than 20%, and improves geometric growth over the untouched control.
Otherwise the immediate four-hour jump-reversal family is removed from active
alpha research; its detector and state-transition components remain reusable.
