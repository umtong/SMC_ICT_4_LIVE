# Session Liquidity Transfer V1 — Final Decision

## Decision

Discard the completed H4 draw → completed 15-minute source-session raid/reclaim → first later five-minute reclaimed-boundary retest candidate.

The implementation, causality, data, execution, and evidence contracts completed cleanly on the frozen BTC first week, so the zero-trade result is a logic result rather than an infrastructure result.

## Frozen first-window funnel

- 12 H4-draw-aligned source-session raid/reclaim candidates;
- 6 had no later source-boundary retest before the route ended;
- 2 reached the opposite source-session liquidity before a retest entry existed;
- 2 first retests invalidated the reclaim;
- 2 first retests held, but the later entry was already outside the required source-session half;
- 0 signals and 0 trades.

## Dominant cause

The additional boundary-retest wait was not a better entry location. In eight of twelve routes the market either delivered toward the objective without returning or consumed the objective before an entry existed. The state machine therefore waited for a pattern that contradicted the intended session-transfer mechanism.

The preregistered ablation could remove only the directional candle-close requirement on an observed retest. No non-directional first-touch rejection dominated the result, so that ablation is not causally justified and is not executed.

## Preserved components

- completed H4 displacement as directional context;
- completed Asia/Europe inventory boundaries as causal intraday liquidity;
- completed destination-session raid and close back inside as the actual scenario confirmation;
- stop beyond the observed raid extreme;
- opposite completed source-session boundary as the first natural intraday objective;
- current shared NAV three-percent planned-loss sizing, costs, funding, liquidation, and NautilusTrader execution.

## Successor hypothesis

A new base candidate will enter only after the completed 15-minute raid/reclaim bar itself, using the first completed ten-second execution bucket strictly after that bar. It will not wait for another direction or entry-location pattern. Entry must still lie in the correct half of the completed source range, the opposite source boundary must remain unconsumed, and cost-after reward/risk must remain at least 1.2. This is a new session-raid-reversal hypothesis, not an ablation result.
