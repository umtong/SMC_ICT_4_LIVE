# Candidate 09 reproducible evaluation

- Status: **GATE_FAIL**
- Gate passed: **False**
- Baseline pooled daily geometric return: **-0.600476%**
- Baseline pooled NAV multiple across sampled days: **0.881192x**
- Baseline trades: **19**
- Maximum sampled-segment drawdown: **10.630265%**

## Fixed-week results

| week | return | daily geo | trades | win rate | PF | max DD | reversal | continuation | implementation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| week-a | -8.5395% | -1.2671% | 8 | 25.00% | 0.520 | 10.6303% | 8 | 0 | OK |
| week-b | -2.3534% | -0.3396% | 2 | 50.00% | 0.221 | 2.9998% | 2 | 0 | OK |
| week-c | -1.3312% | -0.1913% | 9 | 33.33% | 0.927 | 8.7331% | 9 | 0 | OK |

## Gate checks

- PASS — `implementation_ok`
- FAIL — `pooled_daily_geometric_return`
- FAIL — `minimum_trades_each_week`
- FAIL — `all_weeks_positive`
- FAIL — `profit_not_single_trade_dominated`

## Failure classification / structural diagnosis

- Classification: **LOGIC_ERROR_WITH_STRUCTURAL_PATH**
- Largest influence: **midpoint-target**
- Required action: The single-variable ablation midpoint-target improved pooled cost-after growth; revise only that confirmation layer, then freeze and retest.

## Known failure conditions

1. The public kline archive supplies taker-buy flow but not historical L2 replenishment/cancellation; hidden absorption and spoofing can be misclassified.
2. Nautilus bar execution uses adaptive OHLC ordering when both protective prices occur in one minute; trade-tick replay can change those fills.
3. Slippage, impact and funding reserve are charged as an explicit cash-equivalent composite cost; nonlinear capacity impact is not inferred from one-minute bars.
4. The test instrument's static margin model does not reproduce Binance notional tiers or every liquidation rule. Any rejected order is reported, never silently resized.
5. Continuation is skipped when no already-observable opposing liquidity pool exists; price-discovery trends can be missed by design.
6. A gate pass is not a final success: the frozen three-year BTC evaluation must also exceed the cost-after 1% daily geometric criterion without concentration.
