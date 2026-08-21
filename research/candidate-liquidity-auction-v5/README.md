# Candidate Liquidity Auction V5

This candidate tests one integrated skilled-trader decision policy rather than separate OB, FVG or breakout strategies.

The causal sequence is:

1. observe a pre-existing semantic liquidity boundary;
2. classify the interaction as failed auction or accepted auction from completed price/volume evidence;
3. after confirmation and departure, place one limit order at the first return to the known boundary/OB/FVG zone;
4. cancel the unfilled order when the original first-return opportunity is invalidated, its target is consumed, or its causal lifetime ends;
5. after fill, exit only at the predeclared take-profit or stop-loss;
6. arbitrate all four markets through one global pending-order/position slot.

The policy deliberately excludes response-time fields from order selection. `response_kind`, retest extreme, decision-stage features and response-stage features may be retained only for offline leakage audits; they are not inputs to the live decision.

Current fixed geometry:

- failed auction: 1.25R first-return plan; event-extreme invalidation;
- accepted auction: 2.0R first-return plan; transferred-boundary invalidation;
- targets require a causal prior high/low, previous-day level or prior-only 24-hour volume node beyond the planned target;
- transaction costs and stop slippage are included in realized R while the gross planned RR contract remains at least 1.0.

The fresh workflow uses dates not used to design the semantic first-return policy.