# Candidate 4t — diagonal liquidity integration

This work does not create a separate channel strategy. The repository already contains `candidate-diagonal-channel-auction-v1`; Candidate 4t reuses it as a dynamic liquidity-boundary sensor inside the same failed-auction / accepted-auction decision grammar.

A diagonal line or channel is eligible only when it was fitted from confirmed historical pivots before the interaction. The line supplies context and an external liquidity boundary; direction still comes from the price-volume response at that boundary, OB/FVG remain first-return location refinements, and the stop/target remain event invalidation and the first still-live opposing objective.

The integration first records the existing detector's executable API and actual action schema, then adapts only the missing fields into the immutable Candidate 4t action contract. It does not duplicate horizontal and diagonal episodes that describe the same causal interaction.
