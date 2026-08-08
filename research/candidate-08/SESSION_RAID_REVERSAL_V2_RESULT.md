# Session Raid Reversal V2 — terminal result

## Decision

`H4_DRAW_DIRECT_SESSION_RAID_REVERSAL` is rejected. The first corrected BTC week passed, but the
second frozen BTC week produced three trades, zero wins, and three structural-stop exits. The family
therefore did not generalize beyond the discovery week.

## What V2 established

V1 could not test the market scenario because every entry was force-exited after a one-tick entry-cost
under-reserve. V2 corrected that implementation defect without changing the scenario:

- two deterministic ticks are reserved before bar-market quantity sizing;
- the actual fill-to-stop expected loss is checked again after entry;
- raid, reclaim and the next executable bucket are recorded as three causal events;
- a warm-up raid cannot jump across a data gap into the evaluation window;
- current shared NAV, 3% planned loss, 6 bp per fill, official funding and mark price, native
  liquidation, and the one-global-position contract remain unchanged.

The corrected first week, 2024-04-08 through 2024-04-15 UTC, produced four trades, two wins, two
losses, +9.6423% cost-after NAV return, +1.3237% daily geometric growth and 5.2969% maximum realized
NAV drawdown. All execution, risk, funding and causality checks passed.

## Why the family is nevertheless rejected

The second frozen week, 2025-06-09 through 2025-06-16 UTC, produced:

- three signals and three closed trades;
- zero wins;
- three structural-stop exits;
- -8.9519% cost-after NAV return;
- -1.3314% daily geometric growth;
- 8.9519% maximum realized NAV drawdown.

Increasing a stop-slippage reserve or reducing quantity cannot change those three trades from losses
to wins: each scenario reached its structural invalidation before its frozen target. This is therefore
a robust scenario-logic failure, not merely a sizing problem.

There is also a separate baseline risk failure. One native stop fill lost 3,855.0429 USDT against a
3,000 USDT signal-time budget, a ratio of 1.2850. The fill-adjusted expected loss had remained within
budget, so the shifted prior-hour ten-second Q99 reserve underestimated that stop execution. This
risk issue is recorded, but it is not worth polishing for a family that already failed directionally
on all three second-week opportunities.

## No ablation and no third week

The preregistered removal of the source-session-half location rule is not run. The losing second-week
trades already passed that rule, so removing it cannot alter their stop-before-target outcomes and was
not the dominant failure. The third frozen week is not run because the family is already terminally
rejected.

## Preserved infrastructure, discarded alpha

Preserve:

- `BAR_MARKET_TWO_TICK_ENTRY_RESERVE_V1`;
- `DIRECT_RAID_THREE_CAUSAL_EVENTS_V1`;
- `SESSION_RAID_REVERSAL_SIGNALS_V2_CAUSAL_NEXT_BUCKET`.

Discard as trading alpha:

- `H4_DRAW_DIRECT_SESSION_RAID_REVERSAL`.

The next independent hypothesis is a session-opening initial-balance failed auction. It tests whether
new-session price discovery rejects an opening range edge, rather than assuming that an arbitrary
completed-session liquidity raid should reverse.
