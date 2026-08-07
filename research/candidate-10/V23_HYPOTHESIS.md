# Candidate 10 v23 — OI-Semantic Auction Routing

## Failure carried forward from v22

v22 repaired the internal-trigger/external-target hierarchy. Its eight clean
NautilusTrader trades nevertheless produced seven structural-stop losses. Seven
of the eight accepted-break continuations began with falling open interest
classified as `CLEARING`; all seven were economically negative after declared
costs. The implementation, causality, order lifecycle and accounting were clean.

The failure is therefore not a threshold defect. It is a directional-state
defect: position clearing and new position building were allowed to imply the
same continuation scenario.

## Research basis

This generation uses the following literature only to justify the causal state
separation, not to choose thresholds or optimize the evaluated week.

- Cheng, Deng, Wang and Yu, *Liquidation, Leverage and Optimal Margin in
  Bitcoin Futures Markets* (arXiv:2102.04591): forced-liquidation flow is large
  and aggressive in perpetual futures.
- Cont, Kukanov and Stoikov, *The Price Impact of Order Book Events*
  (arXiv:1011.6402): short-horizon price change is linked more directly to net
  order-flow imbalance and available depth than to trade volume alone.
- Alexander, Heck, Kaeck and Riordan, *Order Flow Impact and Price Formation in
  Centralized Crypto Exchanges* (2024): crypto price formation is fragmented,
  and high-frequency cross-market integration can break down.

The resulting inference is deliberately conditional: an OI decline identifies
leverage removal, but it does not by itself establish lasting directional
acceptance. Persistence must be demonstrated by the subsequent auction. If the
old range is reclaimed and executed flow reverses, the forced-flow impulse is
classified as exhaustion rather than continuation.

## Full state grammar

```text
pre-existing structural pool
→ impulsive boundary break with executed flow
→ extreme OI decline = CLEARING
→ continuation entry prohibited
→ wait within the unchanged probe_max_bars window
→ completed bar reclaims the old range by the unchanged confirmation distance
→ executed flow changes to the reversal direction
→ first later raw aggregate TradeTick
→ liquidation-clearing exhaustion reversal
→ stop beyond the actual raid extreme
→ nearest pre-existing completed 8h funding-session external target
```

For an accepted break with `BUILDING`, v22 continuation remains unchanged:

```text
accepted boundary break
→ extreme OI increase = BUILDING
→ second completed bar holds beyond the boundary with same-side flow
→ first later raw aggregate TradeTick
→ continuation toward external session liquidity
```

Existing `REJECTION + CLEARING` logic is also unchanged.

## Exact ablation

`ablation-v22-clearing-continuation` restores only v22's OI mapping:

```text
CLEARING acceptance → continuation allowed
BUILDING acceptance → continuation allowed
```

Full and ablation share exactly the same:

- BTC week selection and seed;
- source pools and one-event-one-scenario identity;
- impulse, volume, flow and OI thresholds;
- completed-bar timing and `probe_max_bars=2`;
- entry on the first later aggregate trade;
- stop buffer and structural invalidation geometry;
- completed eight-hour external target hierarchy;
- taker fees and size-dependent impact;
- whole-account current NAV sizing and 3% planned-loss budget;
- NautilusTrader execution, positions and NAV accounting.

## Falsification

v23 is discarded as a complete candidate when a clean first-week run shows any
of the following without a new structural explanation:

- clearing-reclaim reversals retain materially negative gross or cost-after
  expectancy;
- the semantic split collapses to no meaningful independent opportunity;
- BUILDING continuation remains systematically adverse;
- the exact ablation is equal or superior and the full mapping adds no coherent
  nonlinear value;
- profit, if any, is concentrated in one source event.

No result is rescued by changing OI quantiles, reclaim distance, expiry, target,
risk, fees, selected hours or symbol-specific exceptions.
