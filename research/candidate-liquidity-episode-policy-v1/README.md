# Candidate Liquidity Episode Policy V1

This directory restores the research unit that was missing from the repository.

It continues from `candidate-liquidity-world-model-v1`; it does not restart the
liquidity-auction work from zero and it does not preserve the old policy as a
benchmark. Reused components are limited to point-in-time market preparation,
semantic/directional-change liquidity, destination-first plan geometry, fee and
slippage assumptions, and the one-plan episode contract.

## Trading grammar

```text
public liquidity and market structure
-> causal interaction episode
-> completed price-volume control evidence
-> one first-return location
-> one structural invalidation
-> one fresh opposing destination
-> one pending order
```

The three current mechanisms share that grammar:

- failed-auction reversal after a sweep and reclaim;
- accepted-auction continuation after outside acceptance and a held retest;
- initiative-mitigation continuation inside a still-live larger auction leg.

OB and FVG are entry-origin tools, not standalone strategies. Trend lines,
channels and swing structure define public liquidity and direction context.
Fakeout/trap is the interaction event. Price response, activity, signed flow,
cross-market breadth and relative return are control evidence.

## What is structurally different from the old lattice

- one causal episode can create at most one plan;
- the destination is selected before reward/risk is calculated;
- no proximal/midpoint x fixed-RR plan expansion;
- no hindsight best-plan label;
- no post-fill time exit;
- an unfilled order may die only with the original entry opportunity;
- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT share one pending/position slot.

`episode_policy.py` enriches the existing destination-first episode generator
with causal decision-time market context. `route_episode_policy.py` estimates
fill and target-before-stop probabilities using only chronologically earlier
development windows. The account decision is positive expected log growth at
the fixed 3% risk, not a separate scorecard.

The short workflow is diagnostic. It deliberately uses separated one-week
windows and labels the result as non-continuous. A long continuous run is only
worth the compute after the actual trades and missed opportunities show that
the market logic is coherent.
