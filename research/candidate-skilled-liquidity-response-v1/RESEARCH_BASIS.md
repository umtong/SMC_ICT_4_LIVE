# Research basis and transfers

This file records the ideas which changed the trading decision. It is not a literature review and none of the cited work is treated as proof that this implementation is profitable.

## EasyChart source material

The project source describes one connected decision rather than independent OB, FVG, trend-line and channel strategies.

- Direction and market structure come first. Trend lines describe direction and also concentrate stop liquidity; channels describe a wave range whose outer edges collect liquidity.
- Fake out / Trap is a higher-order event: contraction around a public structure, a liquidity sweep accompanied by unusual activity, and either rapid reclaim or genuine outside settlement.
- OB and FVG are useful at meaningful locations after the direction/event is understood. They refine the return price and define a nearby structural invalidation; neither should create a trade alone.
- Targets are opposing structures or liquidity: previous swing extremes, channel edges, trend lines, OB/FVG or other public pools.
- The included public-trade examples repeatedly combine broad direction, a structural liquidity event and a precise return location. Several exits respond to a price-volume climax rather than a fixed reward multiple.

The implementation transfer is therefore:

```text
public liquidity boundary
    -> observe the attempted break and the price-volume response
    -> infer failed versus accepted auction from settlement
    -> use OB/FVG/source only for first-return entry geometry
    -> invalidate beyond the event
    -> route to still-live opposing liquidity
```

## Market microstructure

### Order-flow imbalance and depth

Rama Cont, Arseniy Kukanov and Sasha Stoikov, *The Price Impact of Order Book Events*:

- https://arxiv.org/abs/1011.6402

They find that short-interval price changes are more robustly related to order-flow imbalance than to raw trade volume, and that the impact coefficient varies inversely with market depth. The implementation does **not** pretend OHLCV is a full order book. It uses actual signed quote flow when available, delta share next, and a body/range-weighted volume proxy only as the sparse-data fallback. Price displacement is always evaluated jointly with effort and local liquidity geometry.

### Resiliency after liquidity shocks

Hai-Chuan Xu, Wei Chen, Xiong Xiong, Wei Zhang, Wei-Xing Zhou and H. Eugene Stanley, *Limit-order book resiliency after effective market orders: Spread, depth and intensity*:

- https://arxiv.org/abs/1602.00731

The paper measures how the book and price recover after liquidity shocks and reports different continuation/resiliency behavior for different shock aggressiveness. The trading transfer is not a fixed post-event horizon. It is the distinction between:

- an energetic overshoot which fails to hold outside and settles back through the boundary; and
- a break which establishes outside occupancy and holds its first pullback.

Kyle Bechler and Michael Ludkovski, *Order Flows and Limit Order Book Resiliency on the Meso-Scale*:

- https://arxiv.org/abs/1708.02715

Their results reinforce that active and passive flow jointly matter and that deeper liquidity shape can matter beyond a single imbalance number. Here that idea appears as two-sided live-liquidity objectives, source strength/confluence and settlement response rather than as a fitted LOB model.

### Intrinsic event time

Anton Golub, Gregor Chliamovitch, Alexandre Dupuis and Bastien Chopard, *Multi-scale Representation of High Frequency Market Liquidity*:

- https://arxiv.org/abs/1402.2198

Directional changes and overshoots provide a state/event representation which is not tied to equal clock intervals. The implementation reuses the project's causal directional-change nodes to define public extremes and event scales, then evaluates the actual response around those events. Clock time remains in the data, but the trading episode is organized by interaction, extreme, settlement and first return.

## Control and signal-processing transfer

A liquidity interaction is modeled as a small step/impulse-response experiment:

- **input:** attempted penetration of a public boundary;
- **effort:** activity, range, body displacement and signed flow relative to prior-only local baselines;
- **state response:** overshoot, outside-close occupancy, boundary crossings, reclaim/settlement, pullback hold and path efficiency;
- **decision:** failed, accepted or initiative auction after the response is observable.

This is deliberately different from adding another indicator score. The variables describe one causal mechanism. Positive surprise is measured by log ratios to medians formed strictly before the interaction, so the same equations adapt to different price levels, assets and volatility regimes without symbol-specific constants.

Ryan Adams and David MacKay, *Bayesian Online Changepoint Detection*:

- https://arxiv.org/abs/0710.3742

The current version does not implement BOCPD. The transferable principle is that state inference should update online from observations available at the current time rather than from full-sample segmentation. A later version may replace some local prior baselines with an online run-length state only if actual trade and no-trade evidence shows that regime adaptation is the limiting problem.

## Ideas deliberately not transferred

- No claim that every wick is institutional manipulation.
- No fixed FVG/OB strategy independent of context.
- No future-best target, target lattice or retrospective plan selection.
- No symbol identity or symbol-specific thresholds.
- No outcome-trained admission model in v1.
- No trade-volume-only directional rule.
- No forced time exit after fill.

The research question is whether the causal settlement response contains enough reusable information to select frequent, high-quality day-trading episodes after realistic costs. Only the repository's actual integrated account results answer that question.
