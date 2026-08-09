# Candidate 57 — source alpha vs independent episodes

Candidate 57 reuses the Candidate 51 NautilusTrader execution/accounting stack and the Candidate 55 source-faithful public `RSI_BB_MACD_Nov_2023_1h_2_Dec` adapter. It does not rebuild ingestion, matching, portfolio accounting, fees, slippage, funding reserve, global-slot arbitration, or 3% current-NAV risk sizing.

The external system is treated as a high-value discovery object, not as proof. Candidate 57 asks a narrower question that Candidate 55 did not isolate directly:

1. `exact_level_short`: preserve the original Python precedence and source-style hourly level re-entry. This is an **alpha replication probe**, but repeated entries while one condition remains true are not accepted as independent project trades.
2. `exact_edge_short`: preserve the same short-side source logic, but allow only a new rising-edge episode. This tests whether the claimed short-side behavior survives the project's causal-independence rule.
3. `exact_edge`: preserve both sides with one entry per rising-edge episode. This is the second project-eligible interpretation.

The two edge variants compete for the project route. The level variant is diagnostic only: strong level results with weak edge results mean that the public result depends on repeated re-entry or insufficiently independent opportunities, not that the implementation necessarily failed.

## Adaptive evaluation ladder

The campaign predeclares all intervals before reading results and only spends more compute on survivors.

- Short development: 2026-07-22 through 2026-07-28. Three variants; mechanics, signal reachability, expectancy, drawdown, and opportunity density are diagnosed.
- Frozen confirmation: 2025-11-03 through 2025-11-09. At most one edge winner plus the level diagnostic survivor.
- Intermediate continuous account: 2025-09-01 through 2025-09-30. Only a confirmed edge winner. The full project gate is applied.
- Long continuous account: 2024-03-01 through 2024-08-27 (180 calendar days). It runs only if the 30-day account already passes the full gate.

The final gate requires after-cost geometric daily growth of at least 1%, completed independent trades at least calendar days, positive expectancy, maximum drawdown at most 20%, positive NAV, no liquidation, no order rejection, and no global-slot/account-contract violation.

## Failure classification

A missing or nonzero run, future-feature rejection, rejected order, multiple simultaneous positions or entry intents, liquidation, non-positive NAV, or a mismatch between Nautilus positions and parsed trades is classified as an **implementation/integration error**.

A mechanically valid run with no signals, no executed opportunities, negative expectancy, negative growth, excessive drawdown, or insufficient independent episode density is classified as a **strategy-logic failure**. Threshold changes are not used to disguise that distinction.

`campaign.py` writes compact reproducible evidence to `research/candidate-57/evidence/`. Full transient backtest outputs remain in the workflow workspace rather than being committed.