# Candidate 09 reproducible evaluation

- Status: **GATE_FAIL**
- Gate passed: **False**
- Baseline pooled daily geometric return: **0.243443%**
- Baseline pooled NAV multiple across sampled days: **1.052387x**
- Baseline trades: **10**
- Maximum sampled-segment drawdown: **5.910882%**

## Fixed-week results

| week | return | daily geo | trades | win rate | PF | max DD | reversal | continuation | implementation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| week-a | 8.0446% | 1.1115% | 6 | 66.67% | 2.245 | 5.9109% | 6 | 0 | OK |
| week-b | -3.0008% | -0.4343% | 1 | 0.00% | 0.000 | 3.0008% | 0 | 1 | OK |
| week-c | 0.4163% | 0.0594% | 3 | 33.33% | 1.066 | 5.9101% | 2 | 1 | OK |

## Gate checks

- PASS — `implementation_ok`
- FAIL — `pooled_daily_geometric_return`
- FAIL — `minimum_trades_each_week`
- FAIL — `all_weeks_positive`
- FAIL — `profit_not_single_trade_dominated`

## Failure classification / structural diagnosis

- Classification: **LOGIC_ERROR_NO_STRUCTURAL_PATH**
- Largest influence: **insufficient cost-after conditional edge or opportunity rate**
- Required action: Discard candidate-09 as a complete candidate; preserve only the listed mechanisms for later hypotheses.
- Parts worth preserving: absorption/reclaim branch produced executable events; acceptance/retest branch produced executable events; risk-budgeted loss path remained recoverable in the gate sample

## Known failure conditions

1. The public kline archive supplies taker-buy flow but not historical L2 replenishment/cancellation; hidden absorption and spoofing can be misclassified.
2. Nautilus bar execution uses adaptive OHLC ordering when both protective prices occur in one minute; trade-tick replay can change those fills.
3. Slippage, impact and funding reserve are charged as an explicit cash-equivalent composite cost; nonlinear capacity impact is not inferred from one-minute bars.
4. The test instrument's static margin model does not reproduce Binance notional tiers or every liquidation rule. Any rejected order is reported, never silently resized.
5. Continuation is skipped when no already-observable opposing liquidity pool exists; price-discovery trends can be missed by design.
6. A gate pass is not a final success: the frozen three-year BTC evaluation must also exceed the cost-after 1% daily geometric criterion without concentration.
