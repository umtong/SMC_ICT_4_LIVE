# Project-wide audit performed after external research

Order: external research in `EXTERNAL_RESEARCH.md` → project branch/code/result audit → Candidate 16 design. Candidate 16 was branched from Candidate 05 commit `e9c858247ef5247bc3f4d8ad3f0de078a7ecebb0`, fixing the reused runner/data snapshot despite concurrent research.

## High-value findings by lineage

| Lineage | Evidence retained | Failure or limitation retained |
|---|---|---|
| Candidate 01 | Causal boundary interaction and rejection/acceptance framing. | Confirmation consumed geometry and entered late. |
| Candidate 02 | Short-window causal state tests. | Strong right-tail weeks did not establish continuation. |
| Candidate 03 | OI contraction and spot/futures alignment as liquidation-vacuum context. | Context alone did not complete a tradeable state. |
| Candidate 04 | Explicit causal state graph and no-trade states. | Whole strategy did not establish target performance. |
| Candidate 05 | Best reusable Binance `aggTrades` + public `bookDepth` feature/data pipeline; Nautilus runner; sponsored CHoCH, confirmed retrace, second touch, reset/reacceleration. V26 fixed weeks were positive. | Continuous evaluation exposed reset dependence, right-tail selection, regime drift, one-slot competition, and insufficient opportunity density. V31 strict impact/resiliency produced almost no events. |
| Candidate 06–08 | Mutually exclusive rejection/acceptance routing attempts. | Surface thresholds or late completion did not create robust opportunity density. |
| Candidate 09 | Strongest fixed 21-day evidence found: Candidate 13 aggregate daily geometric growth `1.3617%`, 16 trades, 12 wins, all three fixed weeks positive; completed source auction, failed-boundary stop, source objective/equilibrium target. | Trade counts were concentrated `9/2/5`; long continuous evidence was not confirmed. |
| Candidate 10 | Positive fixed-week lineage and cross-market causal episodes. | Long runs exposed correlated cascade entries and non-independent opportunities. |
| Candidate 11 | Broad variant testing and failure documentation. | No validated final target system found. |
| Candidate 12 | Rejection, acceptance, and expiry as mutually exclusive completed states. | Five wins in one short week did not survive untouched periods. |
| Candidate 13 | Completed-source-auction semantics and natural targets. | Did not independently establish required long-run growth. |
| Candidate 14 | Most useful long-run falsification. V8 showed seed ≠ completed acceptance; V9 exposed wrong leadership anchoring; V10 moved leadership to the failure leg and improved 84-day NAV from about `0.807x` to `1.095x`. | V10 still had 14 trades, 5 wins, daily growth about `0.108%`, drawdown about `14.35%`. Strong displacement/efficiency and peer unanimity still failed; passive pullbacks caused adverse selection. |
| Candidate 15 | Sequential response router and same-leg geometry. | Five fixed weeks produced one completed trade, a loss. The router decided late and then allowed only one entry bar. |

## Reused

- Candidate 05 causal timestamps, Binance ingestion, feature generation, gap contracts, NautilusTrader `BacktestNode`, fees/slippage/latency/daytrade handling, portfolio/NAV accounting, and 3% current-equity risk sizing.
- Candidate 09/13 principle that invalidation and target belong to a completed source auction and existing objective.
- Candidate 12/14/15 mutually exclusive completed states and explicit no-trade.
- Candidate 14 v10 lesson that evidence belongs to the new failure leg, not the old sweep impulse.

## Not reused

- Fixed-week performance as proof.
- Restart-dependent NAV aggregation.
- Candidate 05 v54 capacity quantity cap.
- Peer unanimity as sufficient confirmation.
- Candidate 15 long sequential score accumulation.
- Fallback targets, score-based risk multipliers, custom backtester, or custom account engine.

## Distinct hypothesis

Candidate 16 keeps Candidate 05’s higher-frequency parent liquidity pool but replaces single-bar classification with a maximum-three-completed-bar effort/result state machine:

- `FAILED_AUCTION`: observable directional effort, limited maximum progress, then completed reclaim.
- `ACCEPTANCE_CONTINUATION`: at least two completed outside closes with sufficient progress and efficiency, then first retest.
- `UNRESOLVED`: window expires or price re-enters without the effort required to call failure.

A completed state is rejected when no unconsumed liquidity objective supplies at least `1.0` net planned-loss unit after costs. This directly targets the repeated project failures of late confirmation and invented/consumed target space.
