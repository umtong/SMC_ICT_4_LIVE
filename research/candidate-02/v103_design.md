# Candidate-02 v103 — Endogenous Turnover-Clock Order-Flow Regimes

## Why this candidate exists

v77 showed that a quarter-hour aggressive-flow burst can produce the required frequency and first-week growth, but the unchanged event changed sign in the locked second week. v102 then showed that adding impact-retention confirmation to the same quarter-hour clock created a hard signal ceiling.

v103 changes the event generator rather than adding another threshold. Market time advances only when completed aggressive quote turnover accumulates. This keeps the useful v77/v102 observations—flow persistence, impact retention, spot/perpetual confirmation and depth resilience—while removing the fixed UTC clock that caused the opportunity ceiling.

## State machine

1. Close a non-overlapping turnover packet using only completed one-minute data and a threshold frozen from lagged prior data.
2. Compare the packet with prior completed packets.
3. Require unusual signed-flow persistence, aligned price movement and path efficiency.
4. Observe the next complete packet.
5. Classify exactly one state:
   - retained-impact price discovery;
   - absorbed-flow exhaustion;
   - ambiguous/no trade.
6. Submit the intent to the existing NautilusTrader path.
7. Size from current account NAV with a fixed 3% planned loss.

## Anti-overfitting controls

- First week selected before collection: 2025-11-17, seed 20260807103.
- Central turnover unit 8; adjacent units 6 and 10.
- No candidate-specific notional cap, leverage cap or score-based risk multiplier.
- One predeclared ablation only: remove the front-depth-refill ceiling.
- Second and third weeks may be opened only after the unchanged central rule and an adjacent clock pass the previous week.
- Performance is never computed outside NautilusTrader.

## Reused evidence and changed assumption

The reusable evidence is that persistent flow, depth withdrawal and a coherent range objective can be useful after a genuinely durable impulse. The discarded assumption is that the impulse must start on a fixed quarter-hour. The endogenous packet clock is the single structural change.

## Immutable source identifiers

- Core Git blob: `ece6aa6bb7bd3e8667f52b4d2669607c4c6fab0e`
- Base configuration Git blob: `c64d2fdbf50d1d08eba2b6ab2ed8a6c3e6d38959`

## Implementation incident before data access

A placeholder core file was accidentally created while preparing the prospective lock. It was replaced by the full core before the workflow or any v103 data collection existed. No strategy output or market data had been observed, so this was a controlled repository-write error rather than a strategy or research-variable change.
