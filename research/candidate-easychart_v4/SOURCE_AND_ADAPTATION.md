# Source and adaptation map

Every executable rule is classified so an implementation guess cannot masquerade as EasyChart teaching.

## Source-explicit

- Naked price/structure is primary; OB, FVG and Fakeout/Trap are used inside a market scenario rather than as unconditional entries.
- OBs at liquidity absorption or a meaningful structure are preferred; the body is the entry zone and the formation wick is an invalidation reference.
- A meaningful FVG has a conspicuously large middle displacement candle, is stronger after a sweep or around an OB, and should not be traded alone.
- Fakeout/Trap requires a move outside an already meaningful structure and a return; confirmation/retest trading is the conservative form.
- A planned area which is never reached is not chased.
- The target is the next opposing structure/liquidity objective.
- Trend lines and channels use wick pivots; a channel is parallel and needs at least three points before the next interaction can be considered.

## Natural human-to-program translations

- A drawn line becomes a finite band because exchange prices have ticks and OB/FVG are zones. The near and far edges define `inside`, `outside`, `partial reclaim` and `full reclaim`.
- “It came back inside” is not treated as a single binary wick event. A partial return remains `UNRESOLVED`; a complete return must clear the entire shared context.
- The first retest is consumed whether it wins, loses, or fails to react. A prettier second retest is not substituted after the fact.
- Nearby overlapping contexts from the same causal episode are not independent trades.

## Research hypotheses, not source claims

- The first implementation uses a 60m context, 15m state decision and 5m execution hierarchy.
- Exact intersection of same-side 60m and 15m OB/FVG zones is used as a machine-auditable high-level context.
- A 15m break needs a next-bar open-and-close hold before being called acceptance.
- Same-timestamp cross-asset signals are routed by causal episode age and stable identifiers rather than an opaque alpha score.

These hypotheses are replaceable when case-ledger mismatches show a better translation. They are never described as the trader's hidden intent.

## Outside-field methods reused

- Critical-decision analysis: reconstruct cues, alternatives, uncertainty and timing from complete trade episodes rather than extracting keywords alone.
- Robust model fitting: when trend/channel geometry is added, consensus across confirmed wick pivots will be preferred to hand-picked perfect points.
- Online state inference: rejection and acceptance are sequential evidence states, not labels assigned from one candle after knowing the outcome.
- Market microstructure: volume/order-flow data is considered only when it discriminates acceptance from rejection or establishes executable liquidity; it is not appended as a generic confirmation filter.
