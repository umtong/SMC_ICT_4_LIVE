# Candidate 10 v20 — Liquidation Auction Rejection / Acceptance

## Status before execution

This is an independent generation replacing v4. The controlled official-L1 rerun removed protective-order errors but v4 still lost 50.1087% of NAV on the first preselected BTC week, with 6 wins and 28 losses. The exact price-only ablation lost 59.2774%. Same-side aggressor flow therefore removed some bad events, but single-event boundary acceptance had negative gross price expectancy and no structural improvement path. v4 is discarded as a trading candidate.

## Economic hypothesis

A pre-existing structural liquidity pool can produce two different auction outcomes when leveraged positions are cleared:

1. **Rejection** — price crosses the pool, large executed flow and range show a genuine liquidity event, open interest clears, price closes back through the boundary, and the next completed bar moves farther inside with opposite executed flow.
2. **Acceptance** — price crosses and closes outside the pool during a leverage shock, then the next completed bar holds or extends outside with same-side executed flow.

The first event is never traded. An executable plan exists only after the second completed five-minute bar confirms the auction result. The target is the nearest pre-existing unconsumed structural pool in the resulting direction.

Structural pools are created only from:

- a completed eight-hour funding session high or low; and
- a five-minute pivot known only after two right-side bars have completed.

## Data and causality

- Binance USD-M five-minute klines provide price, quote volume and taker-buy quote volume.
- Binance USD-M five-minute metrics provide aggregate open interest and taker long/short volume ratio.
- Every metric observation is joined only when `metric.create_time <= completed_bar_time`.
- Raw aggregate trades are replayed in NautilusTrader for the first post-confirmation entry and raw-trade stop/target triggering.
- At equal timestamps, the trade is replayed before the completed bar, so a plan cannot execute on the trade which completed its confirmation bar.

## Execution and risk

- Entry: Nautilus market order on the first raw TradeTick strictly after second-bar confirmation.
- Exit: Nautilus reduce-only market close when raw TradeTick reaches the structural stop or target.
- Planned loss per unit includes entry-to-stop distance, entry and stop taker fees, and causal expected entry/stop impact.
- Quantity is current entire account NAV times 3%, divided by planned per-unit loss.
- No arbitrary nominal cap, leverage cap or score-based risk multiplier is added.
- Positions are flattened before funding timestamps and evaluation close.

## Exact ablation

`ablation-no-oi-state` removes only the extreme open-interest-state requirement. It preserves structural pools, price interaction, executed-flow impulse, second-bar rejection/acceptance confirmation, entry, stop, target, fees, expected impact, seed, Nautilus execution and 3% current-NAV sizing.

## Predeclared gate

The deterministic seed `20260806` selects the same three BTC weeks used throughout candidate 10. The first week is executed first. Remaining weeks run automatically only when the full first-week system passes all predeclared gates: at least 1% daily geometric NAV growth, at least seven closed trades, at least four wins, largest-win concentration no more than 50%, no order errors, causal integrity, and intraday maximum drawdown below 30%.

Implementation errors are fixed under identical week, data, logic, parameters, costs, seed and risk. A clean run with inadequate opportunity or negative expectancy is a logic result and is not rescued by parameter accumulation.
