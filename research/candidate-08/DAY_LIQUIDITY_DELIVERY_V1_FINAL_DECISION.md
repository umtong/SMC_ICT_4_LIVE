# Day Liquidity Delivery V1 — Final Decision

## Decision

The completed-H4-draw → session-route → separate five-minute MSS/FVG → first FVG retrace family is discarded as a promotable candidate.

## Clean first-window evidence

Frozen BTC window: `2024-04-08T00:00:00Z` to `2024-04-15T00:00:00Z`.

Base V1 completed without implementation, causality, risk-budget, funding, liquidation, or residual-exposure failures:

- 14 completed H4-draw/session-route candidates;
- 4 later five-minute MSS plus standard three-bar FVG events;
- 1 first FVG retrace that held;
- 1 executed trade, 1 positive trade;
- total return `+0.8276977689%`;
- daily geometric growth `+0.1178252360%`;
- maximum realized-equity drawdown `0.1155692940%`.

The trade was a directionally correct, risk-valid short raid reversal, but it timed out profitably before the distant completed-day target.  One trade is neither day-trading frequency nor evidence of repeated independent expectancy.

## One allowed ablation

The only removed variable was standard three-bar FVG non-overlap.  The H4 draw, active external context, completed session raid/acceptance route, separate five-minute frozen-swing break, displacement body/range/close-location, first later structural retest, stop, costs, funding, and shared-NAV three-percent risk remained.

Result on the identical week:

- 14 routes;
- 6 five-minute MSS/displacement events;
- 2 first broken-swing retests held;
- 1 cost-valid signal;
- 1 trade, 1 positive trade;
- exactly the same NAV result as the base candidate.

Therefore the standard FVG was not the dominant frequency constraint.  The additional five-minute structure-confirmation layer itself was the binding constraint, and removing one of its representations did not create a structural frequency or growth breakthrough.

## Largest performance drivers

1. **Worked:** completed H4 displacement supplied a stable directional draw.
2. **Worked:** completed source-session raid/reclaim localized a genuine intraday liquidity event.
3. **Worked:** structural stop, costs, funding, shared NAV sizing, and NautilusTrader execution remained inside contract.
4. **Failed:** requiring another independent five-minute structure transition after the already-confirmed session route made the same information pass through a redundant confirmation layer.
5. **Failed:** the distant day/week target was not reached by the sole winning trade inside six hours, so it was a valid directional context but not always the first realizable intraday objective.

## Preserved learning for the successor

The successor is not an ablation result and cannot inherit promotion.  It starts as a new base hypothesis:

- use only H4-draw-aligned source-session raid/reclaim routes;
- treat the completed raid/reclaim as direction confirmation;
- use the first separate five-minute retest of the reclaimed source boundary as execution location, not as another directional classifier;
- stop beyond the observed raid extreme;
- target the first still-unconsumed liquidity in the draw direction, prioritizing the opposite completed source-session boundary before a farther completed day/week level;
- retain costs, causal stop-slippage reserve, funding, current shared NAV three-percent risk, and NautilusTrader execution unchanged.

This is a distinct session-liquidity-transfer hypothesis and must restart at the frozen first BTC week.
