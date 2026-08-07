# Research basis

Candidate 12 uses the following evidence only to define causal hypotheses; none of it is treated as proof of profitability.

- Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*: short-horizon price changes relate to signed order-flow imbalance and available depth. Binance one-minute `taker_buy_volume` is therefore used only as a completed trade-flow confirmation proxy, never as full order-book imbalance or passive replenishment evidence. <https://arxiv.org/abs/1011.6402>
- Osler, *Stop-Loss Orders and Price Cascades in Currency Markets*: stop orders can cluster beyond salient technical levels. Completed session and prior-day extremes are consequently modeled as possible liquidity locations, but a touch alone never predicts direction. <https://doi.org/10.1111/1540-6261.00588>
- Intraday Bitcoin studies report recurring activity/volatility patterns rather than a uniform 24-hour process. Candidate 12 therefore frames completed Asia/London ranges as auction context, while requiring price confirmation instead of treating time as alpha.
- NautilusTrader official backtesting documentation defines event processing, bar limitations, adaptive high/low ordering, and engine-owned execution/accounting. Candidate 12 keeps all matching, fees, margin, positions, and NAV inside NautilusTrader. <https://nautilustrader.io/docs/latest/concepts/backtesting/>

The independent synthesis is an auction decision, not a named candle pattern:

- access + close back inside + structure displacement + held pullback implies a failed auction hypothesis;
- sustained closes outside + held retest + reacceleration implies an accepted auction hypothesis;
- either path is tradable only when its invalidation and a pre-existing structural target remain attractive after modeled costs.
