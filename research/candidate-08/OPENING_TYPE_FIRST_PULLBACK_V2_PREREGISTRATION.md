# Opening-Type First-Pullback State Router V2 — preregistration

## Structural reason for V2

V1 classified initiative acceptance from completed previous-day value and a completed
session initial balance, but invalidated that state whenever a later M5 close rotated
inside the IB. That mixed two different roles: previous value defined the latent state,
whereas the IB edge was only the eventual trigger structure. The frozen V1 BTC week
therefore produced 29 initiative states but no initiative trade; 27 classified states
were terminated before a separate trigger.

V2 is not a threshold relaxation. It preserves V1 context and opening classification,
but makes state validity depend on the previous-value edge that actually defined the
state. It trades only a new leg after a completed first pullback and a separate M5
structure break. The stop belongs to that new leg's pullback extreme; the broader
opening state and the trade-leg invalidation are deliberately separated.

## Fixed decision policy

```text
complete previous UTC-day value
→ complete DST-aware 30-minute session IB
→ OPEN_DRIVE / OPEN_TEST_DRIVE / OPEN_REJECTION_REVERSE / UNRESOLVED
→ first completed post-IB pullback holds the state-defining value edge
→ separate completed M5 displacement breaks pullback and relevant IB structure
→ next contiguous 10-second execution observation
→ pullback-extreme structural stop
→ frozen IB extension or opposite previous-value edge
```

Ten-second observations never select context, state, direction, stop, target, or setup
quality. There is no parameter search and at most one attempt per session.

## Evaluation frozen before execution

Seed `4082027` selected three untouched Monday-to-Monday weeks, each more than 28 days
from earlier candidate development weeks:

1. 2026-01-05 through 2026-01-12 UTC
2. 2024-08-12 through 2024-08-19 UTC
3. 2024-09-30 through 2024-10-07 UTC

The same dimensionless policy is evaluated jointly on BTCUSDT, ETHUSDT, SOLUSDT, and
XRPUSDT under one shared account. Across all four markets, pending new entry orders plus
open positions may never exceed one. Current shared NAV risk remains at most 3%, with
all configured fees, bar-market entry reserve, causal stop slippage, funding, mark
price, native liquidation, and residual-exposure checks.

The first week must produce at least five closed trades, positive cost-after return,
positive-trade share at least 50%, no single winning trade above 50% of positive PnL,
and clean execution contracts before the other two weeks are run. Diagnostic results
are not promotion evidence.
