# External-Liquidity Quote Resiliency V1 — Frozen Research Specification

## Purpose

This candidate tests whether the missing causal variable in the discarded aggregate-trade families
is the response of displayed liquidity supply after an external-liquidity interaction.  It is a new
scenario family, not a parameter relaxation of Flow Response, Intrinsic Repricing or Delayed
Reacceptance.

The detector remains separate from execution.  It may observe completed aggregate trades,
completed top-of-book updates and already-completed 4-hour/day/week external-liquidity levels.  It
may not inspect future paths, orders, fills, positions, PnL or model scores.  NautilusTrader remains
the only backtest and execution engine.

## Immutable evidence set

The official Binance Vision USD-M `bookTicker` catalog was enumerated through paginated S3
ListObjectsV2 before any outcome was inspected.  Every ZIP had to have a matching CHECKSUM object.
Seed `8811` selected the following BTC weeks uniformly without replacement from all contiguous
seven-day starts:

1. 2023-10-15T00:00:00Z through 2023-10-22T00:00:00Z;
2. 2023-07-20T00:00:00Z through 2023-07-27T00:00:00Z; and
3. 2024-02-21T00:00:00Z through 2024-02-28T00:00:00Z.

The first day contains 7,613,922 valid top-of-book updates, no crossed quotes, no nonpositive size,
no nonmonotonic exchange transaction time and no gap above 517 milliseconds.  Ten-second completed
buckets therefore retain dense within-bucket replenishment and withdrawal evidence while matching
the verified native Nautilus runner and existing aggregate-trade bars.  The dates, seed, cadence and
catalog hash may not be changed after results are known.

## Observable quote event decomposition

For consecutive top-of-book observations, quote events are decomposed in base-asset quantity:

```text
bid_add:
  bid improves  -> new bid quantity
  bid unchanged -> positive quantity change

bid_remove:
  bid retreats  -> previous bid quantity
  bid unchanged -> negative quantity change

ask_add:
  ask improves  -> new ask quantity
  ask unchanged -> positive quantity change

ask_remove:
  ask retreats  -> previous ask quantity
  ask unchanged -> negative quantity change

quote OFI = bid_add - bid_remove - ask_add + ask_remove
```

Positive quote OFI means stronger displayed demand or weaker displayed supply.  Negative quote OFI
means weaker displayed demand or stronger displayed supply.  An update cannot reveal passive order
identity and can reflect addition, cancellation, execution or level replacement; interpretation
therefore always combines quote events with aggressive `aggTrades` and price response.

All robust baselines use only prior completed ten-second buckets: shifted one-hour empirical 90th
percentiles for absolute aggressive flow and absolute quote OFI, and a shifted one-hour median for
spread.  A minimum of fifteen minutes is required.  Missing quote-event buckets carry the last
observed quote state forward and zero event flow; no future quote may backfill an earlier bucket.

## Scenario A — Quote-replenished failed auction reversal

At an already-completed external high, the outward direction is up; at an already-completed external
low, it is down.

The state sequence is:

```text
completed external level crossed with outward aggressive pressure
-> up to three completed buckets of liquidity-supply response
-> opposing displayed quote replenishes at least 1.25x its removal
-> cumulative quote OFI opposes the outward direction
-> price closes back through the interacted boundary
-> a separate completed bucket confirms opposite aggressive flow and opposite quote OFI
-> confirmation breaks the frozen reclaim/counter-auction extreme
-> enter opposite the sweep
```

The stop is beyond the full observed sweep/response extreme plus the existing structural buffer.
The target is the nearest active completed external level in the reversal direction.  A target
already reached before confirmation invalidates the setup.

Economic interpretation: aggressive traders reached external liquidity, but opposing displayed
supply replenished faster than it was removed and the auction could not retain the new price area.
The separate confirmation prevents entry on replenishment alone.

## Scenario B — Quote-withdrawal acceptance continuation

The state sequence is:

```text
completed external level crossed with outward aggressive pressure
-> up to three completed buckets of liquidity-supply response
-> opposing displayed quote removal is at least 1.25x replenishment
-> same-side displayed support addition is at least its removal
-> cumulative quote OFI agrees with the outward direction
-> price closes and holds beyond the boundary while spread returns below 1.5x its causal median
-> a later completed retest touches and holds the boundary with weaker pressure
-> a separate completed bucket confirms same-direction aggressive flow and quote OFI
-> confirmation breaks the frozen retest extreme
-> enter with the auction
```

The stop is beyond the observed retest extreme plus the existing structural buffer.  The target is
the nearest active completed external level in the continuation direction.  A reclaimed boundary or
a target reached before confirmation invalidates the setup.

Economic interpretation: opposing displayed liquidity was withdrawn or consumed faster than it was
replenished, while same-side displayed support replaced behind the move.  The retest and separate
confirmation require the new area to remain accepted.

## Shared execution and risk contract

- BTC first; other symbols are not evaluated until the three frozen BTC weeks pass unchanged.
- Current full shared NAV is the sizing base.
- Planned maximum loss is exactly 3% of current shared NAV.
- Expected loss includes entry-to-stop distance, both 6 bp fills, one adverse entry tick, the shifted
  causal stop-slippage reserve and causal funding reserve.
- No arbitrary maximum notional, leverage cap, model score or risk multiplier is permitted.
- Across all assets, pending new entries plus positions remain at most one.
- Market OUO bracket, official funding, mark price, liquidation, order/fill/position/account evidence
  and residual-exposure checks remain native NautilusTrader responsibilities.
- Minimum cost-after external-target reward/risk is 1.2; no fitted R target is allowed.

## Promotion protocol

The first frozen BTC week must have at least three closed trades, positive after-cost return,
complete causal evidence, no execution-contract failure and no residual exposure.  Only then may the
second and third frozen weeks run.  All three must be positive with combined daily geometric growth
at least 1%, positive-trade share at least 45% and no single winning trade contributing more than 50%
of positive PnL before any multiasset or long evaluation.

## Single predeclared diagnostic ablation

After a clean base logic failure, remove only the normalized quote-OFI direction sign gate at
confirmation.  Keep interaction pressure, replenishment/withdrawal ratios, price reclaim/hold,
retest where applicable, structural stop, completed external target, costs, current-NAV 3% sizing
and all native execution contracts.  Diagnostic evidence can justify a newly specified base family
but is never directly promotable.

## Rejection criteria

The family is discarded rather than fitted when:

- both causal scenarios are negative or one never trades and the other is negative;
- targets are not reached after structural invalidation on complete paths;
- increased frequency comes only from the diagnostic ablation and remains negative;
- results require stop widening, target shortening or date/threshold changes after observation; or
- observed quote behavior cannot be distinguished causally from missing or malformed data.
