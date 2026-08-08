# Candidate 16 v2 pre-registration

This file is committed before the first v2 market run.

## Untouched diagnostic

- Build/warm-up: `2025-02-07` through `2025-02-16` UTC.
- Evaluation: `2025-02-10` through `2025-02-16` UTC.
- Instrument: BTCUSDT perpetual.
- One continuous 100,000 USDT account; no daily or weekly reset.
- Current-account-NAV planned-loss budget: 3% per trade.
- Candidate 05/NautilusTrader owns replay, orders, fills, fees, latency, margin,
  liquidation, accounting and NAV.

## Frozen decision policy

1. Context is restricted to completed 15m, 60m and daily source-auction highs/lows.
2. A boundary is consumed once; nearby same-side levels form one causal episode.
3. Directional approach pressure must precede the interaction.
4. Two completed closes plus displacement, flow, efficiency and participation
   establish true acceptance outside the source auction.
5. A later opposite initiative bar must lose that accepted boundary.
6. A still later initiative break or first rejected boundary retest triggers entry.
7. Stop is beyond the failed boundary and the observed failure/trigger extremes.
8. Target is the already-existing source midpoint, otherwise the opposite source
   edge, and must provide at least 1.20 net R after modeled costs.
9. Wick reclaim without acceptance, acceptance without failure, failure without
   a separate trigger, ambiguous two-sided breach, and insufficient target space
   are explicit no-trades.
10. Any liquidation, rejected order, non-positive equity, or global-position
    violation is an integrity failure.  Project gate success remains cost-after
    geometric daily NAV growth >=1% plus the unchanged activity/robustness gates.

Once results are observed, this period becomes development data and will not be
reported as an untouched holdout again.
