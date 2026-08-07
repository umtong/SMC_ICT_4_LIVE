# Candidate-09 v20 — Completed-auction value rejection

## Why the accepted-breakout-failure family is retired

The exact v18 control passed the frozen three-week screen but reduced 100,000 USDT to
8.86 USDT over the predeclared three-year BTC interval. v19 then required a failed
boundary to retest and reject before entry. Of 322 armed failures in the same fixed
weeks, only two became entries; 148 were reaccepted outside and 85 reached equilibrium
before a retest entry. Applying that retest only to the dominant direct path was
cost-after negative. The family therefore lacks a structural route from loss of outside
acceptance to durable opposing liquidity under the available one-minute observations.

## New causal state family

v20 does not add a filter to v18/v19. It defines a new completed-auction value state:

1. Aggregate only a fully completed 60-minute auction.
2. Assign each completed minute's volume to its typical price `(high + low + close) / 3`.
3. Freeze weighted quantiles: value low 15%, equilibrium 50%, value high 85%.
4. During the next completed auction, observe a directional probe beyond one frozen value edge.
5. Require a completed return into value with opposite displacement and aggressor-flow polarity.
6. Require a later retest of that value edge from inside and a completed rejection.
7. Enter only after rejection, target the frozen equilibrium, and invalidate beyond the
   probe/return/retest adverse extreme plus the existing ATR buffer.
8. Expire without chasing if equilibrium is reached first, the edge is reaccepted outside,
   or the existing resolution window elapses.

The one-minute archive does not reconstruct L2 queue depth, replenishment or cancellation.
The typical-price volume distribution is an explicit coarse auction proxy. It is not
labelled as an order-book volume profile.

## Frozen controls

| Variant | Single changed layer |
|---|---|
| `baseline` | volume-weighted value + return + later edge-retest rejection |
| `no-retest` | enter on the completed return into value |
| `range-midpoint` | replace weighted value with range quartile edges and midpoint |
| `no-flow` | remove only aggressor-flow confirmation |

No detector threshold, cost, reward-to-risk gate, risk fraction, fixed week, long period,
or NautilusTrader execution/accounting contract changes.

## Predeclared interpretation

- If baseline is too sparse while `no-retest` is strong, edge-retest confirmation is a
  timing cost and should not be tuned by widening tolerance.
- If `range-midpoint` dominates, the coarse volume distribution contributes no useful
  information and should be removed rather than optimized.
- If `no-flow` dominates, one-minute taker-flow polarity is not a useful confirmation for
  this state family.
- If all variants fail, completed-auction value rejection is discarded as the primary
  alpha source rather than repaired through parameter accumulation.
