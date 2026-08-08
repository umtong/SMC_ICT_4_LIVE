# Opening Drive Acceptance Continuation V1 — preregistration

## Research question

When a major session establishes its first thirty-minute inventory, does a displaced close beyond one
edge, an immediate second outside close, and a later separate successful boundary retest identify a
new accepted auction area that can deliver one full initial-balance extension after realistic costs?

This is not a relaxed version of the rejected opening failed-auction reversal. It tests the opposite
market state: sustained outside-price acceptance and migration, rather than rejection back into the
opening balance.

## Frozen session clock

- Asia: 00:00 UTC;
- London: 08:00 `Europe/London`, converted to UTC separately for each local date;
- New York: 09:30 `America/New_York`, converted to UTC separately for each local date.

The initial balance is exactly six contiguous completed five-minute bars. Any incomplete or
noncontiguous balance is unusable.

## Frozen scenario sequence

At most the first attempt per session is eligible.

1. **Opening displacement** — within the next 180 minutes, a completed M5 bar closes at least 0.05 of
   its completed ATR beyond one IB edge, moves directionally, has body and range at least their
   shifted prior-12-M5 medians, and closes in the directional outer third.
2. **Outside-price acceptance** — the immediately following contiguous completed M5 bar also closes
   outside the same edge. An inside close terminates the attempt.
3. **Separate retest** — a later completed M5 bar touches the accepted edge within 0.05 breakout-bar
   ATR and closes outside. The acceptance bar cannot double as this retest. A close through the
   boundary back into the IB terminates the attempt.
4. **Executable observation** — the first contiguous completed ten-second bucket after the retest
   supplies the market-entry reference. Ten-second data does not choose direction, session, setup
   quality, stop or target.
5. **Structural invalidation** — beyond the most adverse boundary/retest/execution sequence extreme
   plus 0.05 breakout-bar ATR, and no closer than 0.10 ATR from entry.
6. **Frozen objective** — exactly one completed IB width beyond the accepted edge. The attempt is
   rejected if this objective is consumed before order submission.

No same-session retry is allowed after invalidation, target consumption or uneconomic geometry.

## Economic rationale

A single wick or close outside the IB can be a failed auction. Two consecutive completed outside
closes demonstrate persistence, while a separate later retest distinguishes acceptance from a brief
momentum burst. The one-IB extension is determined by the completed opening inventory before the
signal; it is not an outcome-fitted R multiple or a later-chosen liquidity level.

## Cost, risk and portfolio contract

- current shared USDT NAV is the only sizing base;
- planned loss is exactly 3% of that NAV;
- quantity divides the loss budget by entry-to-stop distance, two 6 bp fill charges, the verified
  two-tick bar-market entry reserve, shifted prior-hour ten-second true-range Q99 stop reserve and
  causal funding reserve;
- no notional cap, leverage cap, score multiplier or asset-specific risk parameter;
- NautilusTrader owns orders, fills, funding, margin, liquidation, account, position and NAV;
- at most one pending new entry or open position exists globally.

## Discovery and promotion

No parameter search is permitted. BTCUSDT 2024-04-08 through 2024-04-15 UTC is the first frozen week.
It promotes unchanged only with at least three closed trades, positive cost-after NAV return, and no
execution, risk, funding, liquidation, causality or residual-exposure failure. The two already-fixed
BTC weeks are then run without any rule or parameter change.

All three weeks must be positive, each must contain at least three closed trades, combined positive-
trade share must be at least 45%, no single positive trade may supply more than half of total positive
PnL, and combined daily geometric NAV growth must be at least 1% before longer BTC evaluation.

A clean zero, weak or negative result rejects this market scenario. No threshold is relaxed to fit a
week.
