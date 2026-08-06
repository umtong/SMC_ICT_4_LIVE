# Candidate 01 v29 Result — Rejected

## Frozen hypothesis

`v29` retained the only component which survived v28:

```text
cost-resolved outside aggressive-flow initiative
→ first completed opposite-flow pullback
→ outside value and first measured target remain intact
```

Instead of waiting for a later equal-notional resumption event to complete,
`v29` armed a NautilusTrader `STOP_LIMIT` bracket one BTC tick beyond the
completed pullback extreme. The worst permitted fill was capped 7 bp beyond the
trigger and all quantity, price-risk-share and cost-after reward/risk checks
were performed at that worst price.

The single ablation entered at market immediately after the identical completed
pullback. Detector, state sequence, stop, target, risk and cost were unchanged.

## Infrastructure validation

NautilusTrader 1.230.0 does not accept `STOP_MARKET` as a bracket parent. The
installed fixed wheel was inspected and a `STOP_LIMIT` parent was tested on a
synthetic TradeTick stream:

```text
signal observed
→ stop-limit parent armed
→ later venue trade triggers parent
→ subsequent admissible trade fills within the declared cap
→ take-profit closes position
→ account ends flat
```

The generated authoritative adapter passed the same end-to-end smoke. It uses
official aggregate trades as NautilusTrader TradeTicks and contains no fill,
PnL or NAV simulator.

## Frozen first BTC week

```text
2023-08-28 00:00 UTC
through
2023-09-04 00:00 UTC
```

## Primary STOP_LIMIT result

```text
initiative events                         29
counterflow pullbacks                     21
evaluation instructions                   19
Nautilus submissions                       0
closed positions                           0
INSUFFICIENT_NET_REWARD_RISK rejects      19
```

Operational invariants all passed:

```text
NautilusTrader execution                 true
custom fill/PnL/NAV simulator            false
ended flat                               true
ended without pending entry              true
global entry-gate violations                0
protective-order failures                   0
liquidation markers                         0
```

## Single ablation: immediate market entry

```text
selected plans                            19
Nautilus submissions                       4
closed positions                           4
wins                                       1
win rate                               25.0%
profit factor                          0.448
cost-after total return                -5.02%
geometric daily return                -0.733%
maximum drawdown                       -8.95%
```

The remaining plans were rejected as:

```text
insufficient cost-after reward/risk       13
cost dominated                              1
global position occupied                    1
```

## Failure diagnosis

The conditional-order implementation worked. The failure was structural:

1. The first outside initiative and pullback frequently occurred — opportunity
   detection was not the bottleneck.
2. The full initiative-path stop belonged to the original impulse, not the new
   resumption leg. It was therefore too distant from a pullback-break entry.
3. The first one-range measured target was often already partly traversed by
   the initial impulse before the pullback completed.
4. At the worst stop-limit price all 19 plans failed the cost-after geometry.
5. Removing conditional timing produced only four executable trades and a
   negative direction outcome, so entry timing alone was not a repair.

A direct diagnostic using the completed pullback swing as invalidation improved
geometry, but only four of the nineteen plans cleared the unchanged 1.35
cost-after reward/risk threshold when the first measured target was retained.
Thus simple stop tightening does not create sufficient day-trading opportunity.

## Valid component retained

The following remained useful:

- 29 causal outside initiatives and 21 accepted counterflow pullbacks in one
  week provide adequate raw opportunity density;
- conditional entry at the completed pullback extreme is implementable and
  realistic through the fixed NautilusTrader STOP_LIMIT contract;
- risk can be sized at the worst permitted fill so actual loss cannot exceed the
  3% plan merely because of a better fill;
- target-first, invalidation-first and expiry cancellation are operational.

## Decision

`v29` is rejected. Weeks 2 and 3 and long evaluation were correctly blocked.

The next scenario treats resumption as a separate auction leg:

```text
invalidation = completed counterflow pullback swing
objective    = next unconsumed expansion node, not the partly consumed first node
```

Its single ablation retains the pullback-swing invalidation and changes only the
expansion objective.
