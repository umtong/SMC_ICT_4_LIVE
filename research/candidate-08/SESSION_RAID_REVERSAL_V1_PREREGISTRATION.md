# Session Raid Reversal V1 — Preregistration

## Hypothesis

A completed destination-session raid and reclaim of the completed source-session boundary opposite an already-established H4 draw is itself the directional confirmation. Waiting for another five-minute direction pattern or boundary retest can miss the actual transfer.

## Frozen long sequence

1. Completed H4 bullish displacement through a causally confirmed H4 swing establishes the draw.
2. A complete Asia range exists before Europe, or a complete Europe range exists before US.
3. A completed destination-session 15-minute bar trades below the source low by at least `0.05 × causal 15m ATR` and closes back above it.
4. The opposite source-session high was not traded by the raid bar.
5. The first completed ten-second bucket strictly after the completed 15-minute raid/reclaim supplies the market-entry timestamp. Ten-second data is not an alpha condition.
6. Entry must be inside the lower half of the completed source range.
7. Stop is below the observed raid extreme plus `0.05 × causal 5m ATR`.
8. Target is the nearest known unconsumed liquidity above entry: the completed source high or an already-frozen closer completed day/week/H4 level.
9. Cost-after reward/risk must be at least 1.2.

Short is symmetric around the completed source high, upper-half entry, raid-high stop, and source low or closer frozen HTF target.

## Expiry and risk

The setup is cancelled if the source opposite boundary is already consumed, entry is outside the required source half, target geometry is invalid, or cost-after reward/risk is below 1.2. After entry the existing NautilusTrader layer enforces current shared NAV × 3% planned-loss sizing, 6 bp per fill, one adverse entry tick, causal stop reserve, official funding and mark price, native liquidation, one global order/position, six-hour timeout, and window-end flattening.

## Frozen evaluation

First BTC week: `2024-04-08T00:00:00Z` to `2024-04-15T00:00:00Z`.

Only after first-gate success:

- `2025-06-09T00:00:00Z` to `2025-06-16T00:00:00Z`;
- `2025-09-29T00:00:00Z` to `2025-10-06T00:00:00Z`.

First gate: at least three closed trades, positive cost-after return, and no execution, causality, risk, funding, liquidation, or residual-exposure failure.

Three-week gate: every week positive with at least three trades, positive-trade share at least 45%, no positive-trade concentration above 50%, combined daily geometric growth at least 1%, and no execution failure.

## Single allowed ablation

Only if a clean first-window failure is dominated by entry being outside the required source-session half, remove only the half-location requirement. All direction, raid/reclaim, target, stop, costs, funding, risk and execution contracts remain. The result is diagnostic-only and cannot be promoted directly.
