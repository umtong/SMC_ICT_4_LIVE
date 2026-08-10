# Public MBE2 anatomy campaign v2

## Reused public system

Candidate 57 reuses the public `myshortingstrategiembe2.py` system from `remiotore/ccxt-freqtrade` (source blob `d312e07abc99ffd5631a992fc67a4e97a8768c0a`) and adapts it to the project execution/account contract rather than treating the published headline result as evidence.

Source decisions retained:

- 5-minute signal cadence with 140-candle startup.
- Long: RSI(14) crosses above 30 while TEMA(9) is at/below the Bollinger middle and rising.
- Short: RSI(14) crosses below 70 while TEMA(9) is above the Bollinger middle and falling.
- Source ROI schedule: 0m 7.9%, 15m 4.7%, 41m 3.2%, 114m 11%, 180m 0.7%, 420m 0.1% in source profit space.
- Trailing activation at 2.5%, trailing distance 1.5%, and source stop at 22% in source profit space.
- Source-effective leverage approximately 6.46x, with a 10x comparison.

Project adaptation:

- BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT share one continuous account and one global entry/position slot.
- Planned stop loss is sized from current account NAV at exactly 3%, including entry/stop fees, adverse slippage and funding reserve.
- NautilusTrader owns matching, contingent orders, fees, positions, liquidation and portfolio accounting.
- Causal one-minute management avoids same-bar activation/hit hindsight.

## Why v2 is not a gate tournament

The earlier campaign ranked a positive development screen, confirmed only one winner, and then either advanced or killed it. That procedure could discard a low-frequency high-quality component or promote a frequent but weak component. V2 instead runs every predeclared system variant on both the development interval and a separate confirmation interval, then decomposes each result before deciding what is reusable.

The seven variants are:

1. source-faithful both-side 6.46x control;
2. long-only 6.46x;
3. short-only 6.46x;
4. source-faithful both-side 10x;
5. ROI-only management;
6. trailing-only management;
7. a structural repair of the discontinuous 114-minute ROI jump (`0.11` to `0.011`).

No variant is rejected because its trade count is low, and no variant is preferred because it has many trades or take-profits. Mechanical validity and strategic merit are reported separately.

## Anatomy retained per completed trade

The campaign reconstructs the complete causal scenario and records:

- symbol, side, entry/exit, exit cause and holding time;
- net and gross PnL, commissions, return on NAV and R multiple;
- planned loss integrity, MFE and MAE in price and source-profit space;
- router competition, rejected alternatives and score gap;
- RSI cross magnitude, TEMA slope and TEMA/Bollinger displacement;
- Bollinger width, volume participation, 1h/4h/8h returns, 2h-to-8h trend alignment, realized volatility and one-hour range;
- UTC session, cost burden, concentration and causal-episode independence.

The output contains symbol, direction, exit, holding-time, session, score, signal-shape, trend, volatility, collision, excursion and cost slices. Cross-period synthesis uses a role-balanced Pareto set: quality anchor, growth/robustness anchor, low-frequency quality anchor, cost-efficiency anchor and a frequency reference. The frequency reference is explicitly descriptive, not an automatic recommendation.

## Evaluation intervals

- Development: 2026-07-22 through 2026-07-28.
- Untouched confirmation for this campaign: 2025-02-10 through 2025-02-16.
- Each interval receives two preceding warm-up days which are not scored.

Outputs are persisted under `research/candidate-57/evidence/mbe2-anatomy-v2/` as per-case evidence, `synthesis.json`, `manifest.json`, and `RESULT.md`.
