# Session Value Migration Continuation V1 — preregistration

## Research question

Does a major session deliver a durable continuation when price and its cumulative volume-weighted
trading center both migrate beyond the completed prior-day value range, then a separate five-minute
retest holds the accepted value edge?

This candidate is independent from opening-range edge patterns. It requires migration of traded
value, not merely a candle close or liquidity sweep.

## Completed prior-day value

For each UTC day, a usable profile requires exactly 288 contiguous completed five-minute bars.
Typical price `(high + low + close) / 3` is weighted by completed bar volume.

```text
previous-day VWAP = weighted mean typical price
sigma = weighted standard deviation around that VWAP
value low = VWAP - sigma
value high = VWAP + sigma
value-range width = value high - value low
```

All references are fixed after the UTC day is complete and before the next session begins.

## Frozen session clock

- Asia: 00:00 UTC;
- London: 08:00 `Europe/London`, converted to UTC separately for each local date;
- New York: 09:30 `America/New_York`, converted to UTC separately for each local date.

Each session has a four-hour opportunity window and at most one eligible attempt.

## Frozen causal sequence

1. **First outside value close** — the first directional completed M15 close lies at least 0.05
   prior-day sigma beyond one completed value edge.
2. **Value acceptance** — the immediately following contiguous completed M15 bar also closes outside
   the same edge, and cumulative session VWAP through that bar is outside the edge in the same
   direction. Either an inside second close or a session VWAP that remains inside terminates the
   attempt.
3. **Separate M5 retest** — a later completed M5 bar touches the accepted edge within 0.05 completed
   M5 ATR and closes outside. A close through the edge back into prior value terminates the attempt.
4. **Executable observation** — the first contiguous completed ten-second bucket after the retest
   supplies the market-entry reference. Ten-second data does not choose direction, session, setup
   quality, stop or objective.
5. **Structural invalidation** — beyond the most adverse accepted-edge/retest/execution sequence
   extreme plus 0.05 retest-bar ATR, and no closer than 0.10 ATR from entry.
6. **Frozen objective** — one complete prior-day value-range width beyond the accepted edge. The
   objective is fixed before the session and the attempt is rejected if it is consumed before order
   submission.

A failed, invalidated, already-delivered or uneconomic attempt is not retried in the same session.

## Economic rationale

Two outside closes show time acceptance, but price can remain detached from where volume is actually
transacting. Requiring cumulative session VWAP to cross the same completed value edge distinguishes a
migrating auction center from a temporary excursion. The later separate retest asks whether the old
value boundary has changed role from resistance/support into an accepted edge. The projected one-
value-range extension is determined by the completed prior-day distribution rather than by a fitted
R multiple or later outcome.

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
BTC weeks are then run unchanged.

All three weeks must be positive, each must contain at least three closed trades, combined positive-
trade share must be at least 45%, no single positive trade may supply more than half of total positive
PnL, and combined daily geometric NAV growth must be at least 1% before longer BTC evaluation.

A clean zero, weak or negative result rejects the scenario. Distribution boundaries, acceptance and
retest conditions are not relaxed to fit a week.
