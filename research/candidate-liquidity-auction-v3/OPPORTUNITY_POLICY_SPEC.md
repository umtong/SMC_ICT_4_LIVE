# Causal opportunity policy

The system treats each public-liquidity interaction as one causal episode. It does not enter on a sweep, breakout, FVG, order block, or volume spike by itself. It waits for one of several mutually interpretable state transitions:

1. failed auction: probe beyond a public boundary, reclaim, efficient inward initiative, genuine pullback, defended reacceleration;
2. initiative continuation: efficient break from prior balance, controlled shallow pullback, renewed impulse before excessive extension;
3. accepted-boundary continuation: sustained value outside the boundary, first return to the boundary, defense and reacceleration;
4. compression release: contraction followed by directional order-flow expansion, pullback and renewed control;
5. trend pullback: persistent multiscale path with lower-activity countertrend return to rolling value, followed by renewed control;
6. boundary absorption: aggressive outward flow fails to move price through a public boundary and the opposite side takes control.

Every action is emitted only after a completed confirmation bar and enters no earlier than the next bar. Stop is outside the event or pullback invalidation. A trade exists only when a previously visible route objective lies beyond the planned target. All families share normalized features and one period- and year-blocked router; symbols do not receive separate strategy rules. Duplicate signals from the same causal episode are clustered, and one global account chooses at most one action.

Offline future-excursion labels are used only to find missed opportunities and generate charts for human-style case inspection. They are excluded from the runtime feature path.
