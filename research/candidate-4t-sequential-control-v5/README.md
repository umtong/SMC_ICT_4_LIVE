# Candidate 4t v5 — competing auction hypotheses

Version 5 closes the remaining mismatch between the causal market story and the sequential belief state.

A source-boundary interaction can first look accepted and later fail, or first look rejected and later transfer control. The prior filter stored one posterior per episode, so evidence from an accepted-continuation hypothesis could leak into a later failed-auction reversal (or the reverse). Version 5 maintains belief separately by `(family, side)`, decays inactive hypotheses, and resets the active hypothesis on an explicit contradictory control transfer.

The action-independent ownership label is also made more literal. When the harvester provides an immediate/market action, its target-before-event-invalidation outcome owns the state label because it asks whether control existed from the decision state itself. Retest entry variants still train fill and resolution, but they no longer average their entry geometry into direction. A state without an immediate action falls back to the resolved action consensus.

Everything else is inherited from the leakage-safe v4 policy: exact-route geometry, no trade below 1 gross R, same-episode continuation, global slot commitment value, causal pending replacement, immutable post-fill TP/SL, one account and 3% NAV structural risk.
