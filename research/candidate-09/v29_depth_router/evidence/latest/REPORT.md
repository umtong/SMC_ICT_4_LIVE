# Candidate 09 reproducible evaluation

- Status: **GATE_FAIL**
- Gate passed: **False**
- Baseline pooled daily geometric return: **-0.706755%**
- Baseline pooled NAV multiple across sampled days: **0.075109x**
- Baseline trades: **128**
- Maximum sampled-segment drawdown: **93.247467%**

## Fixed-week results

| week | return | daily geo | trades | win rate | PF | max DD | reversal | continuation | implementation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| development-2023 | -92.4891% | -0.7068% | 128 | 14.06% | 0.221 | 93.2475% | 15 | 113 | OK |

## Gate checks

- PASS — `implementation_ok`
- FAIL — `pooled_daily_geometric_return`
- FAIL — `minimum_total_trades`
- PASS — `minimum_active_weeks`
- PASS — `profit_not_single_trade_dominated`
- FAIL — `recoverable_drawdown`
- PASS — `minimum_active_months`

## Failure classification / structural diagnosis

- Classification: **LOGIC_ERROR_NO_STRUCTURAL_PATH**
- Largest influence: **insufficient cost-after conditional edge or opportunity rate**
- Required action: Discard candidate-09 as a complete candidate; preserve only the listed mechanisms for later hypotheses.
- Parts worth preserving: absorption/reclaim branch produced executable events; acceptance/retest branch produced executable events

## Known failure conditions

1. The public kline archive supplies taker-buy flow but not historical L2 replenishment/cancellation; hidden absorption and spoofing can be misclassified.
2. Nautilus bar execution uses adaptive OHLC ordering when both protective prices occur in one minute; trade-tick replay can change those fills.
3. Slippage, impact and funding reserve are charged as an explicit cash-equivalent composite cost; nonlinear capacity impact is not inferred from one-minute bars.
4. The test instrument's static margin model does not reproduce Binance notional tiers or every liquidation rule. Any rejected order is reported, never silently resized.
5. Continuation is skipped when no already-observable opposing liquidity pool exists; price-discovery trends can be missed by design.
6. A gate pass is not a final success: the frozen three-year BTC evaluation must also exceed the cost-after 1% daily geometric criterion without concentration.
