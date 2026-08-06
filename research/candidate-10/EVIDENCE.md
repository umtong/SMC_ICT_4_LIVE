# Candidate 10 Evidence

## Promotion status

**Not promoted.** Candidate v0 failed the cost-after first-week gate and is discarded. Candidate v1 is the active controlled structural revision. No partial result is presented as project-goal success.

## Reproducible failed evidence retained

- Workflow: `31086615230`
- Commit: `ac6ffbb2e79dfe32997572fb9438a73d632c2791`
- Engine: NautilusTrader 1.230.0
- Week: `2023-10-16` through `2023-10-22`, selected before results
- Data: 11,520 verified Binance BTCUSDT perpetual 1-minute bars, zero gaps and duplicates
- Full v0: geometric daily NAV growth `-1.5825%`, 12 trades, 4 wins, net return `-10.5655%`
- Acceptance ablation: geometric daily NAV growth `-2.2108%`, 12 trades, 3 wins, net return `-14.4865%`
- Full v0 price PnL before commissions was positive, but `22,498.1130 USDT` of declared commissions/cost reserve overwhelmed it
- Exact failure analysis: `V0_FAILURE.md`

## Evidence required before candidate promotion

1. exact commit and successful workflow run;
2. pinned environment and `smc4 doctor` success;
3. verified source-data manifest and zero-gap report;
4. net NAV metrics after the declared cost and fill model;
5. turnover, maker/taker fill, reported commission, and price-PnL-before-commission diagnostics;
6. trade count, win distribution, drawdown, and profit concentration;
7. causal scenario event log with no future-time or broken-state-chain violation;
8. actual entry/exit, holding time, MFE, MAE, and exit-class trade ledger;
9. full candidate versus the one-variable acceptance-path ablation;
10. first-week gate pass before the other two preselected weeks;
11. all short gates supporting continuation before longer evaluation;
12. longer BTC evaluation followed by unchanged-logic ETH, SOL, and XRP transfer;
13. one integrated portfolio proving at most one pending parent/position across all four instruments.

The first-week promotion gate remains cost-after geometric daily NAV growth of at least 1% with sufficient independent trades, non-concentrated wins, no order errors, and recoverable drawdown. Growth above 1% is neither reduced nor capped.
