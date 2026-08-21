# Directional Liquidity Policy v2

This branch is a structural replacement for the candidate-plan/classifier lineage, not a threshold revision of it.

## Trading decision

One policy is applied unchanged to BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT:

1. Build a causal multi-scale price/volume direction and a two-sided fresh-liquidity objective map.
2. Wait for one completed causal mechanism:
   - failed-auction sweep/reclaim reversal,
   - accepted-auction break/hold/first-retest continuation,
   - initiative displacement/mitigation continuation.
3. Select exactly one family-specific first-return location. OB/FVG are entry-location evidence, not independent strategies.
4. Put the stop beyond the event or transferred-boundary invalidation.
5. Select the nearest still-live opposing liquidity before checking RR.
6. Trade only when that real destination pays at least 1.0 gross R after the geometry is fixed.
7. Cluster correlated symbols into one market-wide causal episode, arbitrate only among plans known in the same minute, and allow one pending order or position for the whole account.
8. Size the single entry so stop execution, estimated fees and stop slippage cost approximately 3% of current NAV. The implied leverage uses the full account as margin.
9. After fill, exit only at the predeclared take-profit or stop-loss. There is no scale-in, scale-out, forced time exit, daily loss limit or fallback entry.

## What was removed

- candidate target/stop lattices and hindsight best-plan selection;
- class-weighted or uncalibrated probability gates;
- symbol identity as a decision feature;
- weekly policy resets;
- counting the same cross-symbol cascade as several independent trades;
- fixed-R targets when a fresh market destination is absent.

## Files

- `directional_context.py`: causal multi-scale price/volume direction and asymmetric liquidity objective.
- `directional_liquidity_policy.py`: one event-to-entry-to-invalidation-to-destination plan.
- `route_directional_policy.py`: causal cross-symbol episode clustering, global-slot arbitration and continuous NAV.
- `risk_sizing.py`: exchange-precision-aware 3% structural-risk quantity and implied leverage.
- `nautilus_strategy.py`: shared NautilusTrader backtest/live execution bridge: one limit entry, reduce-only stop-market, reduce-only TP limit, local sibling cancellation.
- `episode_policy_exec.py`: adapter to the existing point-in-time data/episode harvester.
- `self_check.py`: quick causal, sizing, clustering and account tests.

## Diagnostic commands

```bash
PYTHONPATH=research/candidate-directional-liquidity-policy-v2:research/candidate-liquidity-episode-policy-v1:research/candidate-liquidity-world-model-v1:... \
python research/candidate-directional-liquidity-policy-v2/self_check.py
```

The branch workflow harvests short, separated market windows only to expose implementation and market-logic errors cheaply. It publishes actual episode rows, selected trades, conflicts and no-trade opportunities. Those windows are development diagnostics, not a long continuous performance claim.
