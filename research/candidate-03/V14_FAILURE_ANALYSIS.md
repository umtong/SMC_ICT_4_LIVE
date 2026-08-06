# NT-LVCFR-v14 frozen validation failure

## Status

V14 is discarded as a complete candidate.  It passed the two development weeks,
was frozen by source blob identity, and then failed the third BTC week
`2022-05-16` through the same NautilusTrader 1.230.0 execution/accounting path.
No detector, strategy, risk, fee, slippage, funding, or engine parameter was
changed after the freeze.

## Frozen third-week evidence

- GitHub Actions run: `31110950496`
- Artifact: `8971689477`
- Artifact digest: `sha256:957a247e6713e7f215be66f6c6bba99cf61b9fb6eed70e934ca91d8a4fff2b06`
- Source events: 19
- V11 priority-router signals: 7
- CHoCH fallback signals: 5
- Native independent episodes: 12
- Wins: 5
- Win rate: 41.6667%
- Initial NAV: 100,000 USDT
- Final NAV: 93,606.04326260 USDT
- Net return: -6.3939567%
- Daily geometric NAV growth: -0.9394909%
- Mean episode PnL: -532.8297 USDT
- Mark-to-market maximum drawdown: 12.0643%
- Native orders / positions: 24 / 12
- Rejected orders: 0
- Single-slot contract: satisfied
- End state: flat

The run therefore represents a valid logic failure rather than an incomplete or
invalid engine run.

## State contribution

| Scenario state | Episodes | Net native PnL (USDT) |
|---|---:|---:|
| `RANGE_MIGRATION_RECLAIM_REVERSAL` | 3 | -1,298.733 |
| `UNROUTED_EVENT_RANGE_CHOCH_FALLBACK` | 5 | -2,364.160 |
| `VALUE_EDGE_CONTINUATION` | 4 | -2,731.064 |

All three executed branches were negative.  The failure is therefore not
explained by one newly added fallback branch.

## Required core-variable ablation

V14's only new core variable relative to the already frozen V11 candidate was:

```text
V11 NO_TRADE event
    + first completed opposite event-range break
    -> CHoCH fallback reversal
```

Removing that fallback reproduces the V11 router.  V11 had already been replayed
on the same third week and failed:

- 7 episodes
- final NAV 95,881.64 USDT
- daily geometric growth -0.59899%
- mean episode PnL -588.34 USDT
- maximum drawdown 9.451%

The ablation removes part of the loss but leaves the candidate below zero with
insufficient events.  Thus the fallback is harmful in this week, but it is not
the root cause of the family failure.  There is no structural path from V14 to
the project target by keeping the V11 priority router and tuning the fallback.

## Largest performance drivers

1. **A completed boundary break was treated as terminal evidence.**  Both V11
   and V14 inferred continuation or reversal before proving that the market
   could hold beyond the boundary.
2. **Flow and price impact were not jointly conditioned.**  A futures-led break
   can be a transient liquidation wave, while a spot-supported break with
   persistent aggressive flow has a different causal interpretation.
3. **Repeated break attempts lacked hysteresis.**  A failed same-side break
   could be followed by another classification without first establishing a
   genuinely new independent auction.
4. **Direction accuracy did not compensate for payoff asymmetry.**  Several
   small structural winners coexisted with full initial-stop losses, producing
   a negative mean episode despite five winners.

## What remained useful

The following components are retained as research infrastructure or atomic
concepts, not as evidence that V14 is profitable:

- the frozen OI-contraction event as a high-information auction trigger;
- prior dealing-range equilibrium and external liquidity as causal waypoints;
- completed-minute observations rather than intrabar hindsight;
- one scenario ID per source event and a strict single portfolio slot;
- native NautilusTrader orders, fills, fees, funding, positions, margin,
  portfolio accounting, and NAV;
- current-NAV 3% planned-loss sizing including expected entry/stop costs.

## Successor hypothesis

The successor must replace the router rather than add another threshold:

```text
OI-contraction event range
    -> opposite completed close: CHoCH reversal
    -> same-side completed close: pending only
    -> second completed close remains outside
       + cumulative futures aggressive flow agrees
       + cumulative spot aggressive flow agrees
       -> acceptance continuation
    -> failed same-side hold/flow test: no second continuation attempt
       -> reversal or expiry only
```

This is the V15 flow-impact auction hypothesis.  It separates boundary crossing
from persistent acceptance and adds hysteresis so one auction cannot generate
repeated nominally independent breakout attempts.
