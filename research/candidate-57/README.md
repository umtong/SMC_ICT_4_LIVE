# Candidate 57 — reuse first, expand only when earned

Candidate 57 reuses the Candidate 51 NautilusTrader execution/accounting stack and the Candidate 55 source-faithful public `RSI_BB_MACD_Nov_2023_1h_2_Dec` adapter. It does not rebuild ingestion, matching, portfolio accounting, realistic cost handling, the four-symbol global slot, or the 3% current-NAV risk budget.

Candidate 55 already ran 2026-07-22 through 2026-07-28. Candidate 57 preserves that artifact-derived result in `evidence/seed_candidate55_picasso.json` instead of spending compute to reproduce it. In the independent `exact_edge` mode, five positions were completed and all five had positive realized PnL; one XRP short remained open at the endpoint. The marked open exposure made the seven-day continuous NAV negative. That is not a pass, but it is enough evidence to justify one frozen confirmation rather than immediate rejection.

Only rising-edge entry modes are eligible. Re-entering while one source condition remains true is never counted as independent opportunity frequency.

## Warmup and evaluation contract

Every new replay loads eight calendar days before the scored interval. The same NautilusTrader account and strategy process receive those bars, but `evaluation_start_ns` prevents warmup trading. This gives the 1-hour indicators enough causal history without silently discarding the first ~65 hours of each scored interval. NAV metrics are calculated only for the declared evaluation dates.

## Adaptive ladder

1. **Baseline frozen confirmation:** `baseline_edge`, 2025-11-03 through 2025-11-09.
2. **Baseline intermediate:** only a confirmed baseline reaches one continuous account on 2025-09-01 through 2025-09-30.
3. **Baseline long:** only a full 30-day project-gate pass reaches 2024-03-01 through 2024-08-27 (180 days).
4. **Structural repair after a logic failure only:** when mechanics remain valid and the entry mechanism is reachable, compare the warmup baseline control with three predeclared alternatives on the reused development interval: a cost-clearing exit floor, ROI-only management, and short-side rising-edge isolation.
5. **Adapted frozen confirmation:** the best positive alternative must survive the distinct untouched interval 2025-05-05 through 2025-05-11.
6. **Adapted intermediate:** only a confirmed adaptation reaches 2024-10-01 through 2024-10-30.
7. **Adapted long:** only a full adapted 30-day pass reaches the same 180-day continuous account.

Short confirmation may tolerate negative endpoint NAV only when completed-position expectancy is positive and an open marked position explains the endpoint. The 30-day and 180-day gates never waive continuous NAV: after-cost geometric daily growth must be at least 1%, independent completed positions must be at least the number of calendar days, completed-position expectancy must be positive, drawdown at most 20%, NAV positive, and every execution/account contract valid.

## Implementation error versus logic failure

A nonzero process, missing or inconsistent reports, future-feature rejection, order rejection, multiple simultaneous entry intents or positions, liquidation, non-positive NAV, nonfinite metrics, unparseable closed PnL, or disagreement between Nautilus and the position report is an **implementation/integration error**.

A mechanically valid replay with unreachable signals, no executable entries, insufficient independent completed positions, non-positive completed-position expectancy, negative continuous growth, excessive drawdown, or failure of the 1% gate is a **strategy-logic failure**. The campaign records those classes separately and does not hide them with a broad threshold sweep.

Run `campaign.py`. Compact evidence is committed under `research/candidate-57/evidence/`; large transient outputs remain workflow artifacts.
