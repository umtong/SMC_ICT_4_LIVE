# Candidate 09 reproducible evaluation

- Status: **GATE_FAIL**
- Gate passed: **False**
- Baseline pooled daily geometric return: **-0.434152%**
- Baseline pooled NAV multiple across sampled days: **0.912680x**
- Baseline trades: **3**
- Maximum sampled-segment drawdown: **3.000073%**

## Fixed-week results

| week | return | daily geo | trades | win rate | PF | max DD | reversal | continuation | implementation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| week-a | -2.9998% | -0.4342% | 1 | 0.00% | 0.000 | 2.9998% | 0 | 1 | OK |
| week-b | -3.0001% | -0.4342% | 1 | 0.00% | 0.000 | 3.0001% | 0 | 1 | OK |
| week-c | -2.9995% | -0.4341% | 1 | 0.00% | 0.000 | 2.9995% | 0 | 1 | OK |

## Gate checks

- PASS — `implementation_ok`
- FAIL — `pooled_daily_geometric_return`
- FAIL — `minimum_trades_each_week`
- FAIL — `all_weeks_positive`
- PASS — `profit_not_single_trade_dominated`

## Failure classification / structural diagnosis

- Classification: **LOGIC_ERROR_NO_STRUCTURAL_PATH**
- Largest influence: **insufficient cost-after conditional edge or opportunity rate**
- Required action: Discard candidate-09 as a complete candidate; preserve only the listed mechanisms for later hypotheses.
- Parts worth preserving: acceptance/retest branch produced executable events; risk-budgeted loss path remained recoverable in the gate sample

## Known failure conditions

1. The public kline archive supplies taker-buy flow but not historical L2 replenishment/cancellation; hidden absorption and spoofing can be misclassified.
2. Nautilus bar execution uses adaptive OHLC ordering when both protective prices occur in one minute; trade-tick replay can change those fills.
3. Slippage, impact and funding reserve are charged as an explicit cash-equivalent composite cost; nonlinear capacity impact is not inferred from one-minute bars.
4. The test instrument's static margin model does not reproduce Binance notional tiers or every liquidation rule. Any rejected order is reported, never silently resized.
5. Continuation is skipped when no already-observable opposing liquidity pool exists; price-discovery trends can be missed by design.
6. A gate pass is not a final success: the frozen three-year BTC evaluation must also exceed the cost-after 1% daily geometric criterion without concentration.
