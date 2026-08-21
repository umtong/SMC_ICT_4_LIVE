# Structural Auction Control

This branch is a structural replacement for the liquidity-auction plan lattices and the two-owner channel-only prototype. It preserves the causal EasyChart geometry and NautilusTrader account/execution layer, but changes the actual decision policy.

## One decision, not tool strategies

A pre-existing 15-minute trend line, channel edge, repeated horizontal defense, or major swing owns one causal auction episode. OB/FVG is only the one-minute footprint of that episode. It never originates an independent strategy.

The episode state is:

`public structure -> first interaction -> rejection or apparent acceptance -> hold/retest -> same-episode Trap when acceptance fails -> displacement/control transfer -> first footprint response -> immutable plan`

The critical change is that a failed apparent acceptance is no longer thrown away after the source has already been claimed. It reverses ownership inside the same episode and must complete a Trap reclaim. This directly represents delayed fakeouts, double tests, and traders trapped beyond an apparently accepted break without duplicating trades from the same liquidity event.

## Price and volume are one event

A visual reclaim or footprint response cannot produce a plan by itself. On the completed first-return decision bar, meaningful aggressive flow must attack against the intended trade while price refuses to move through structural invalidation and closes back in control. This is direct passive absorption: effort points one way, but the auction result points the other way.

Pure aligned initiative is deliberately not treated as equivalent confirmation. In the existing causal first-return evidence it commonly arrived after price had already departed the source and behaved as late chase rather than a high-quality entry. The absorption relation uses causal rolling activity and delta baselines, not an optimized global volume threshold.

## Geometry and execution

- Direction/context: inherited causal 60m BOS plus 60m structure/OB/FVG reversal area; 15m local direction blocks repeated-defense fades against active local initiative.
- Context structure: causal wick-pivot trend lines, parallel channels, repeated horizontal defense, major swing liquidity.
- Entry: first event-local OB/FVG response or exact accepted-break retest after state and absorption confirmation.
- Stop: complete interaction extreme, trigger invalidation, latest confirmed 5m counter swing, and completed retest extreme as applicable.
- Target: nearest still-fresh causal 5m/15m obstacle, exact channel objective, or first extension midline.
- Trade contract: one full position, pre-entry stop/target, gross RR at least 1R, no partials, no clock exit, no stop/target movement.
- Account: existing NautilusTrader four-market continuous account, one global position, 3% risk by quantity, actual fees and configured slippage.

## Diagnostic intent

The first runs are deliberately short and include complete plan, trade, event-trace, and missed/non-trade evidence. They determine whether the new state transition and price-volume mechanism produce better decisions before any long continuous run is justified. There is no scorecard or promotion framework.
