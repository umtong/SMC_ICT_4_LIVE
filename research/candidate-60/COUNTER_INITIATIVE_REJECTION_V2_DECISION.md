# Candidate 60 — delayed factor-owned counter-initiative rejection V2 decision

## Status

**The exact V2 policy is retired. The latent-state decomposition is preserved. Policy-fresh data remain untouched.**

This is not a binary rejection because the proposed 15-minute return was
negative, nor is it a direction reversal because the exact opposite return was
positive in the single completed event. One event cannot identify a direction,
a tradable distribution, or a durable state policy.

Evidence:

- GitHub Actions run: `31484398342`
- causal-funnel run: `31484870848`
- development: `2026-06-29` through `2026-07-05` UTC
- proposed one-slot events: `1`
- proposed 15-minute gross / net: `-7.1461 / -27.1461 bp`
- peer-only delayed-rejection one-slot events at 15 minutes: `399`
- peer-only 15-minute gross / net mean: approximately `-1.09 / -21.09 bp`
- policy-fresh `2026-08-03` through `2026-08-09`: not consumed

## What the causal funnel establishes

The V2 funnel was:

| causal stage | count |
|---|---:|
| scored parent timestamps | 673 |
| broad price and flow direction aligned | 524 |
| strong parent leader exists | 351 |
| leader persists through the next 5m | 133 |
| strong opposite peer counter-burst exists | 6 |
| full peer rejection within 10m | 2 |
| first full rejection retains factor ownership | 1 |
| completed proposed entry | 1 |

The six counter-bursts separated into three distinct states:

1. **No completed rejection within ten minutes — 4 events.**
   These cannot be called failed initiatives and correctly remain
   `UNRESOLVED / NO TRADE`.
2. **Full peer rejection after two minutes but zero contemporaneous factor
   owners — 1 event.**
   The peer burst failed locally, but the original market-wide factor no longer
   owned the auction. This belongs to a broader market-transition or reversal
   state, not to factor resumption.
3. **Full peer rejection after seven minutes with all three other assets still
   factor-aligned — 1 event.**
   This was the only V2 trade and it lost in the proposed direction at every
   recorded horizon.

Thus the factor-ownership condition did not merely suppress an abundant winner
set. It distinguished two economically different completed rejections. The
single factor-owned example, however, does not demonstrate that factor
resumption is profitable.

## Why neither direction is accepted

The one proposed trade was:

```text
broad factor up
→ ETH strong leader and persistent
→ SOL strong opposite five-minute burst
→ seven minutes later SOL price and aggressor flow turn up
→ counter-burst midpoint reclaimed
→ BTC, ETH and XRP all still aligned up
→ next-open SOL long
```

Its gross returns were negative at 5, 15, 30 and 60 minutes. The exact opposite
signed returns were therefore positive, but this does not support a short
policy:

- there is only one observation;
- the proposed and opposite results are algebraic mirrors of the same path;
- the event was selected by a policy designed for factor resumption, not for a
  factor-owned bull trap;
- no independent market explanation predicted that the rejection should fail
  before the return was observed.

Changing direction after seeing this event would be pure outcome fitting.

## What the peer-only control establishes

The peer-only delayed-rejection family was abundant but cost-negative across all
horizons. This rules out the generic claim:

```text
strong five-minute price-and-flow burst
→ later price-and-flow midpoint rejection
→ fade the original burst
```

A completed failed initiative is not sufficient by itself. It may complete only
a local inventory adjustment while the subsequent remaining price space is too
small, or it may be a delayed response to a larger common shock. Adding more
magnitude thresholds to the same representation is not justified.

## Market-model correction

The useful result is a state taxonomy, not a tradable rule:

```text
strong peer counter-burst
→ no rejection: initiative remains accepted or unresolved
→ rejection with factor abandoned: broad market transition
→ rejection with factor retained: local peer failure inside common-factor state
```

V1 showed that transition completion takes multiple minutes. V2 showed that even
when a local rejection completes and the broad factor still appears aligned,
raw one-minute price and aggressor-flow signs do not establish that exploitable
factor resumption remains after entry.

The missing information is no longer another timing or sign threshold. It is the
*economic identity and remaining force* of the move:

- was the common factor driven by information, hedging, or forced deleveraging;
- did the peer burst consume or merely temporarily receive liquidity;
- did marginal price impact accelerate or decay;
- did derivatives-specific pressure diverge from spot/index value;
- was enough unconsumed objective space left after confirmation and costs.

## What is preserved

- explicit `UNRESOLVED / NO TRADE` during the transition window;
- temporal separation of parent state, peer initiative, rejection, and entry;
- contemporaneous cross-asset ownership rather than a stale leader label;
- state decomposition into no-rejection, factor-abandoned rejection, and
  factor-retained rejection;
- deterministic one-slot arbitration, exact opposite attribution, and
  peer-only control;
- the reserved August policy-fresh interval.

## What is retired

The following exact policy is closed:

```text
broad factor + persistent leader
→ strong opposite peer burst
→ within ten minutes price-and-flow midpoint rejection
→ at least two other assets still factor-aligned
→ next-open trade toward the factor
```

Do not tune the ten-minute window, two-of-three ownership count, midpoint,
leader/peer thresholds, symbols, direction, or primary horizon on the consumed
periods. The family is also too sparse to satisfy the final system's required
opportunity density even if a narrow subset later proves useful; at most it
could become a specialist.

## Next research direction

Move to information that identifies participant constraint and auction force
more directly. The next candidate studies forced-liquidation and derivatives
pricing mechanics using existing project data-engineering components:

```text
forced futures pressure and open-interest clearing
→ mark/index or futures/spot dislocation
→ spot acceptance versus resistance
→ marginal impact persistence versus decay
→ later continuation, unresolved state, or liquidation-exhaustion reacceptance
```

This is not another filter on the counter-initiative policy. It is a new state
model intended to distinguish exogenous information shocks from endogenous
leverage clearing before any entry policy is built.
