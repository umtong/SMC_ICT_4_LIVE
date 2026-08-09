# Candidate 57 — reuse first, expand only when earned

Candidate 57 reuses the Candidate 51 NautilusTrader execution/accounting stack and the Candidate 55 source-faithful `RSI_BB_MACD_Nov_2023_1h_2_Dec` implementation. It does not rebuild data ingestion, matching, portfolio accounting, realistic costs, one-global-slot arbitration, or 3% current-NAV risk sizing.

Candidate 55 already spent the compute needed to diagnose 2026-07-22 through 2026-07-28. Candidate 57 preserves that result in `evidence/seed_candidate55_picasso.json` rather than rerunning it. The important seed finding is mechanically valid `exact_edge` behavior with five profitable closed positions out of six, but a negative seven-day continuous NAV because one short remained open and marked against the account at the endpoint. That is neither a project pass nor a reason to discard the mechanism before one frozen confirmation.

Only rising-edge entry modes are project eligible. Source-style level re-entry while one condition remains true is never counted as independent opportunity frequency.

## Adaptive evaluation ladder

1. **Frozen confirmation:** run the untouched baseline `exact_edge` on 2025-11-03 through 2025-11-09.
2. **Baseline intermediate:** only a confirmed baseline reaches the 30-day continuous account on 2025-09-01 through 2025-09-30.
3. **Baseline long:** only a full 30-day project-gate pass reaches the 180-day continuous account on 2024-03-01 through 2024-08-27.
4. **Structural repair only after failure:** when the entry mechanism remains mechanically valid and reachable but baseline exits fail, test three predeclared alternatives on the reused development interval: a cost-clearing exit floor, ROI-only management, and short-side rising-edge isolation.
5. **Adapted confirmation and new intermediate:** the best positive structural alternative must survive the same frozen week and then a distinct 30-day account on 2024-10-01 through 2024-10-30 before long validation.

Short and confirmation screens may tolerate negative endpoint NAV only when completed-position expectancy is positive and an open marked position explains the endpoint. The 30-day and 180-day gates never waive continuous NAV: after-cost geometric daily growth must be at least 1%, independent completed positions must be at least calendar days, expectancy must be positive, drawdown at most 20%, NAV positive, and all execution/account contracts valid.

## Implementation error versus logic failure

A nonzero process, missing metrics or diagnostics, future-feature rejection, order rejection, multiple entry intents or open positions, liquidation, non-positive NAV, nonfinite metrics, or disagreement between Nautilus positions and parsed trades is an **implementation/integration error**.

A mechanically valid run with unreachable signals, no executable entries, inadequate independent opportunity density, non-positive completed-position expectancy, negative continuous growth, excessive drawdown, or failure of the 1% project gate is a **strategy-logic failure**. The campaign records these classifications separately and does not hide them with a parameter sweep.

`campaign.py` integrity-checks and executes `campaign_v2.py.gz.b64`. Compact reproducible evidence is committed under `research/candidate-57/evidence/`; large transient reports remain workflow artifacts.