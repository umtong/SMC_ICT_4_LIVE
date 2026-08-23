# Candidate 4t v6 — causal auction trajectories

The earlier branches already supplied confirmed liquidity boundaries, complete event-time states and sequential entry decisions. The remaining representation gap was that the ownership classifier still saw each state mainly as a flat snapshot. A skilled trader reads the path: whether control is strengthening, stalling, retracing efficiently, or contradicting itself across successive observations.

Version 6 adds only causal trajectory information. For each `(episode, family, side)` it builds current-versus-prior changes, prior-EMA gaps, distance from prior extrema, short consistency, update count, elapsed episode time and phase-change count. Duplicate entry actions do not create duplicate state transitions. Every feature uses the current state and earlier states only; a different future row cannot alter an earlier feature.

The v5 market-state ownership label, competing-hypothesis filter, leakage-safe continuation models, global slot commitment value and one-account route remain unchanged. This is the final short/medium candidate architecture before evidence-driven trade and no-trade inspection.
