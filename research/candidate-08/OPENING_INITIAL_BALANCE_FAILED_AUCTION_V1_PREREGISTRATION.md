# Opening Initial-Balance Failed Auction V1 — preregistration

## Research question

Does the first thirty minutes of a major trading session define an opening inventory whose first
failed edge auction can support a repeatable, cost-after move toward the opposite initial-balance
edge?

This is independent from the rejected direct-session-raid family. It does not assume that any prior
session high or low should reverse. It asks whether **new-session price discovery itself** attempted
and rejected a price outside its completed opening balance.

## Frozen sessions

The session opens are calendar-aware, not fixed summer UTC approximations:

- Asia: 00:00 UTC;
- London: 08:00 `Europe/London`, converted to UTC for that local date;
- New York: 09:30 `America/New_York`, converted to UTC for that local date.

The initial balance is exactly six contiguous completed five-minute bars. Missing or noncontiguous
bars make that session unusable.

## Frozen scenario sequence

For each completed initial balance, at most the first attempt is eligible.

1. **Edge probe and rejection** — within the next 180 minutes, a completed five-minute bar trades at
   least 0.05 of its completed ATR beyond exactly one IB edge and closes strictly back inside the IB.
2. **Separate displacement** — a later completed five-minute bar moves away from the failed edge,
   closes through the sweep bar midpoint, has body and range at least their shifted prior-12-bar
   medians, and closes in the directional outer third.
3. **Executable observation** — the first contiguous completed ten-second bucket after the separate
   five-minute confirmation supplies the market-entry reference. It does not choose direction,
   session, setup quality, stop or target.
4. **Invalidation** — stop beyond the most adverse observed sweep-confirmation-execution sequence
   extreme plus 0.05 sweep-bar ATR and no closer than 0.10 ATR from entry.
5. **Objective** — the opposite edge of the already-completed initial balance. The setup is rejected
   if that edge was consumed before order submission.

A failed attempt is not retried in the same session.

## Cost and portfolio contract

- current shared USDT NAV is the sole sizing base;
- planned loss is exactly 3% of that NAV;
- quantity divides the loss budget by entry-to-stop distance, two 6 bp fill charges, the verified
  two-tick bar-market entry reserve, shifted prior-hour ten-second true-range Q99 stop reserve and
  causal funding reserve;
- no notional cap, leverage cap, score multiplier or asset-specific risk parameter;
- NautilusTrader owns orders, fills, funding, margin, liquidation, account, position and NAV;
- at most one pending new entry or open position exists globally.

## Frozen discovery and promotion

No parameter search is allowed. BTCUSDT 2024-04-08 through 2024-04-15 UTC is the first frozen week.
It promotes unchanged only with at least three closed trades, positive cost-after NAV return, and no
execution, risk, funding, liquidation, causality or residual-exposure failure. The two already-fixed
BTC weeks are then run unchanged. All three must be positive with sufficient trade count before any
longer evaluation or cross-asset transfer.

A clean weak or negative result is a scenario-logic result. Thresholds are not relaxed to fit the
week.
