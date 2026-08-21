# Skilled Liquidity Response v1

This candidate is a structural replacement for the weighted-direction/filter lineage. It is not a threshold revision of `directional-liquidity-policy-v2`.

## Decision model

The same policy is applied to `BTCUSDT`, `ETHUSDT`, `SOLUSDT` and `XRPUSDT`.

A public high, low, trend line, channel edge, previous-day extreme, semantic pool or directional-change node is treated as a **liquidity boundary**. An interaction with that boundary is treated as a small causal experiment:

1. **Input / effort** — attempted break, quote activity, candle range, body displacement and signed flow, normalized only by observations available before the interaction.
2. **Observed response** — overshoot, time spent outside the boundary, crossings, first pullback, reclaim or outside settlement, and path efficiency.
3. **Auction interpretation** —
   - overshoot which settles back inside: failed-auction reversal;
   - break which settles outside and holds its first pullback: accepted-auction continuation;
   - initiative displacement which survives its first mitigation: initiative continuation.
4. **Location** — one family-specific first-return source/OB/FVG location. OB and FVG do not create a trade by themselves.
5. **Invalidation** — beyond the event extreme or transferred boundary which makes the interpretation false.
6. **Destination** — nearest still-live opposing liquidity, selected before RR is checked.
7. **Execution decision** — trade only when the completed response, broader direction and live objective agree and the real destination pays at least 1.0 gross R.
8. **Account** — one entry, one predeclared stop, one predeclared target, approximately 3% current-NAV risk, one pending order or position across all four markets, and one trade per cross-symbol causal episode.

The key distinction is **settlement rather than touch**. A sweep is not bullish or bearish because a line was crossed. Direction comes from what the price-volume auction accomplished after spending effort at the boundary.

## Why this structure

The source material consistently places direction and liquidity above entry tools: trend lines and channels describe market structure and their outer edges collect liquidity; Fake out/Trap is the higher-order event which breaks and reclaims those structures; OB/FVG refine the return price after that event. The public trade examples also repeatedly combine direction, structure and an event, then use OB/FVG for entry and nearby structure/liquidity for exit rather than trading any object in isolation.

Market-microstructure work reaches a compatible abstraction from another direction: short-horizon price changes respond more directly to order-flow imbalance and available depth than to raw trade volume alone, while liquidity-resiliency research distinguishes shocks which reverse from shocks which permanently reprice the market. This candidate converts those ideas into causal bar/flow measurements which remain usable when full order-book data is unavailable.

## Files

- `auction_response.py` — prior-only effort normalization and failed/accepted/initiative response measurements.
- `response_event_detection.py` — causal event completion and local-event de-duplication.
- `skilled_liquidity_policy.py` — event, direction, entry, invalidation and destination joined into one plan.
- `episode_policy_exec.py` — executable adapter to the existing point-in-time market-data and source harvester.
- `route_skilled_policy.py` — market-event clustering, one global account slot and continuous 3% risk NAV.
- `self_check.py` — deterministic causal invariance and response-shape checks.
- `RESEARCH_BASIS.md` — the exact conceptual transfers and external primary sources used.

## Reproduce

The branch workflow runs inside the repository's pinned NautilusTrader research image. The equivalent command is:

```bash
export PYTHONPATH=research/candidate-skilled-liquidity-response-v1:research/candidate-directional-liquidity-policy-v2:research/candidate-liquidity-episode-policy-v1:research/candidate-liquidity-world-model-v1:research/candidate-liquidity-auction-v2:research/candidate-liquidity-auction-v7:research/candidate-liquidity-auction-v6:research/candidate-liquidity-auction-v5:research/candidate-coherent-auction-system-v4:research/candidate-coherent-auction-system-v3:research/candidate-coherent-liquidity-policy-v2:research/candidate-coherent-liquidity-policy-v1:research/candidate-hierarchical-liquidity-bpr-v2:research/candidate-hierarchical-liquidity-bpr-v1:research/candidate-liquidity-displacement-v1:research/candidate-auction-dislocation-confluence-v1:research/candidate-derivatives-dislocation-v1:research/candidate-auction-episode-policy:research/candidate-auction-event-v2:research/candidate-direct-auction-policy:research/candidate-easychart_re1:research/candidate-easychart-v5:research/candidate-easychart-v3

python research/candidate-skilled-liquidity-response-v1/self_check.py

python research/candidate-skilled-liquidity-response-v1/episode_policy_exec.py \
  --start 2025-02-01 --end 2025-02-04 --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache .cache/skilled-liquidity-response-v1-smoke \
  --output artifacts/skilled-liquidity-response-v1-smoke/dev-2025-feb

python research/candidate-skilled-liquidity-response-v1/route_skilled_policy.py \
  --root artifacts/skilled-liquidity-response-v1-smoke \
  --output artifacts/skilled-liquidity-response-v1-smoke/result
```

The short workflow window exists to expose implementation and market-logic errors cheaply. Its exact rows, trades, no-trades and account path are committed under `research_results/skilled_liquidity_response_v1/latest` after a successful run. It is not presented as a long-period performance claim.
