# Opening Type State Router V1 — preregistration

## Research question

Can a complete previous-day value distribution plus a complete session initial balance distinguish
initiative acceptance from responsive rejection before a separate post-IB trade trigger, producing
repeatable cost-after day-trading alpha without ten-second directional inputs?

## Why this candidate

Previous candidate evidence repeatedly showed that a single sweep/reclaim pattern mixed genuine
failed auctions with continuing price discovery.  It also showed that waiting for a later retest often
consumed the target before entry.  Market Profile opening-type logic offers a state router rather than
another filter stack:

- open outside previous value + completed IB value remains outside -> initiative auction;
- open outside previous value + completed IB returns inside -> responsive rejection;
- open inside value or mixed IB evidence -> unresolved/no trade.

The state is complete before the separate five-minute trigger.  Entry, stop and target belong to the
new post-IB leg.  Ten-second data is execution-only.

## Frozen logic

- Previous value: exactly 288 contiguous completed M5 bars from the previous UTC day, using
  volume-weighted typical-price VWAP +/- one weighted sigma.
- Session clocks: Asia 00:00 UTC, London 08:00 Europe/London, New York 09:30 America/New_York.
- Initial balance: exactly six contiguous completed M5 bars.
- OPEN_DRIVE: open outside previous value and the full IB remains outside.
- OPEN_TEST_DRIVE: open outside previous value, IB tests it, but IB close and IB VWAP remain outside.
- OPEN_REJECTION_REVERSE: open outside previous value, IB close and IB VWAP return inside.
- UNRESOLVED: no trade.
- Initiative trigger: separate completed M5 displacement beyond the IB edge.
- Responsive trigger: separate completed M5 displacement through IB midpoint toward previous value.
- Initiative target: one IB extension.
- Responsive target: opposite previous-value edge.
- One attempt per session.
- Minimum cost-after reward/risk: 1.2.
- Risk: current shared NAV maximum planned loss 3%.
- All fees, bar-market crossing, fill slippage, stop reserve, funding, mark price and liquidation are
  handled by the existing native NautilusTrader execution path.

## Frozen evaluation windows

Selected before execution with seed 4082026 after excluding previously used weeks plus/minus 21 days.

1. 2025-08-18T00:00:00Z to 2025-08-25T00:00:00Z
2. 2025-01-20T00:00:00Z to 2025-01-27T00:00:00Z
3. 2026-05-18T00:00:00Z to 2026-05-25T00:00:00Z

The first window must have at least three closed trades, positive cost-after return and clean execution
contracts before the other two windows run.  No threshold search is permitted after observing a
window.  If one family is positive and the other negative, that result may justify a new family-only
base candidate but is not itself promotion evidence.
