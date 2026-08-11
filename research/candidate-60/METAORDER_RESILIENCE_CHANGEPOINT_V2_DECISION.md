# Candidate 60 — metaorder resilience change-point V2 decision

## Status

**The exact V2 trade policy is retired. Its stopping-time and objective-consumption findings are preserved. The reserved fresh interval remains untouched.**

This decision uses only the already-consumed `2026-04-13` through
`2026-04-19` development interval. It does not authorize or consume the
reserved `2026-08-03` through `2026-08-09` interval.

Evidence:

- successful GitHub Actions run: `31523241217`
- evidence commit: `41867a8b96f3e5a99442caca55e7ba06cf46330c`
- exact V1 provisional states reproduced: `7`
- V2 resolved states:
  - `OBJECTIVE_CONSUMED_NO_TRADE`: `5`
  - `CONFIRMED_GEOMETRY_REJECTED`: `2`
- V2 geometry-eligible one-slot trades: `0`
- diagnostic ending NAV: `1.0`
- diagnostic geometric daily growth: `0.0`

## What was tested

V2 retained the exact V1 parent-run detector and changed only the invalid
inference from first marginal decay to immediate reversal. The new state model
required:

```text
persistent parent execution
→ provisional marginal decay
→ sequential force change
→ opposite flow
→ opposite-side book resilience
→ partial reclaim after the final observed extreme
→ next-open reversal
```

The implementation directly called the immutable V1 `_build_symbol_events`
function. The seven source events and their identities matched exactly, so this
result is a strategy-logic result rather than a parent-detector implementation
difference.

## Predicted loss group was changed correctly

V1 contained three events with the following shape:

```text
immediate reversal entry
→ source bracket stop
→ positive cost-adjusted reversal at 60 or 120 minutes
```

V2 transformed all three before any fresh data were used:

- two became `OBJECTIVE_CONSUMED_NO_TRADE`;
- one became `CONFIRMED_GEOMETRY_REJECTED`.

Thus V2 did not merely reduce trade count at random. Fresh parent-direction
extremes and delayed confirmation removed the exact premature-entry group that
motivated the redesign. The state correction is therefore informative even
though the policy is not tradable.

## Why the policy still fails

The same evidence establishes a stronger constraint:

> price-confirming exhaustion generally arrives after the reachable natural
> reversal objective has already been consumed, or after the remaining reward
> is too small relative to costs and the updated invalidation distance.

Five of seven states reached the parent-run VWAP objective before all
confirmation conditions aligned. The two remaining states confirmed, but both
failed the predeclared cost-aware geometry requirement.

### SOLUSDT confirmed state

- provisional: `2026-04-19 13:07:59.999 UTC`
- confirmation: `2026-04-19 13:35:59.999 UTC`
- delay: `28` minutes
- post-provisional parent-direction extreme updates: `8`
- same-direction resumption minutes: `6`
- entry: `86.46`
- parent-run VWAP objective: `86.0186541364`
- updated stop: `86.9087`
- gross reward: `51.0462` bp
- net reward after the 20 bp friction floor: `31.0462` bp
- planned loss including costs: `71.8968` bp
- cost-aware reward/risk: `0.4318`
- geometry verdict: rejected

The path later touched the objective and produced `+31.1770` bp net under the
diagnostic bracket, but that hindsight outcome cannot override the frozen
geometry rejection. At entry time the remaining natural reward did not justify
the updated structural invalidation.

### XRPUSDT confirmed state

- provisional: `2026-04-13 22:54:59.999 UTC`
- confirmation: `2026-04-13 22:59:59.999 UTC`
- delay: `5` minutes
- entry: `1.3742`
- parent-run VWAP objective: `1.3727126176`
- updated stop: `1.3768935`
- gross reward: `10.8236` bp
- net reward after the 20 bp friction floor: `-9.1764` bp
- geometry verdict: rejected

The objective was touched in the entry minute, but the move was smaller than
the friction floor and the diagnostic bracket was `-9.1705` bp net. This is an
objective-space failure, not an exit-management problem.

## What the two confirmed paths do and do not show

Both confirmed paths were positive in the proposed direction at 120 minutes:

- SOL: `+65.9571` bp net at 120 minutes;
- XRP: `+72.8471` bp net at 120 minutes.

The opposite direction was negative for both. This supports the qualitative
claim that the sequential state model identified genuine later reversal
pressure in these two cases. It does not support a deployable policy because:

- there were only two confirmations;
- both were shorts following upward parent runs;
- neither passed the frozen natural-objective geometry;
- one event contributed about 77% of the absolute bracket PnL;
- the one-slot account completed zero trades.

Changing the target to a farther price only because the 120-minute returns were
positive would be outcome fitting. A farther objective requires an independent
market-value or inventory-transfer explanation before it can be tested.

## Market-model correction

The development evidence separates three economically different states:

1. **Fast reversal / objective already consumed.** The reversal exists, but
   waiting for price confirmation leaves no tradeable residual objective.
2. **Confirmed later reversal with insufficient remaining geometry.** The
   direction can be right while the trade is still invalid because the final
   extreme expands risk faster than the remaining natural reward.
3. **No demonstrated later reversal.** The parent move can resume or remain
   accepted; a provisional decay is not exhaustion.

The missing information is therefore not a looser CUSUM threshold or a shorter
confirmation window. The next representation must identify passive absorption
and asymmetric liquidity *before* visible price reclaim spends the reward.

## Preserved components

- direct reuse of the exact parent-run detector;
- every fresh parent-direction extreme resets exhaustion evidence;
- sequential stopping-time evidence instead of a one-bar decay trigger;
- explicit `UNRESOLVED / NO TRADE` and `OBJECTIVE_CONSUMED / NO TRADE`;
- opposite-side depth replenishment as a distinct auction-state observation;
- updated structural stop based on the last observed parent extreme;
- natural-objective geometry checked before outcome;
- exact four-asset episode collapse, one-slot arbitration and 3% planned-loss
  diagnostic contract;
- untouched August fresh reservation.

## Retired components

Retire this exact policy:

```text
V1 persistent parent run
→ robust one-sided CUSUM force decay
→ opposite flow + three-minute book resilience
→ at least 10% price reclaim after the final extreme
→ next-open reversal to parent-run VWAP
```

Do not tune the CUSUM allowance or threshold, reclaim fraction, depth window,
confirmation timeout, stop buffer, cost floor, target, symbol or direction on
the consumed interval.

## Next research direction

The next action is a consumed-data forensic trace, not another trade-policy
sweep. For every V1 provisional state, record the completed-minute path of:

- fresh parent-direction extremes;
- parent versus opposite aggressor flow;
- marginal directional return and efficiency;
- impact per unit of aggressive flow;
- opposite-side versus same-side depth replenishment;
- premium and open-interest changes;
- distance to parent-run VWAP and pre-run value centroids;
- the exact time at which the V1 source stop, source target and later reversal
  occur.

The purpose is to determine whether the fast target group, the premature-stop
later-positive group, and the genuine non-reversal group separate *before*
price reclaim. Only a pre-outcome observable that predicts those exact group
changes can become the next frozen state policy.

A promising successor would be lifecycle-conditioned absorption under load:
continued parent-direction aggressive flow with collapsing marginal price
response and replenishing opposite liquidity. That is only a hypothesis until
the forensic trace demonstrates a causal separation. It must not be promoted
merely because it is plausible.
