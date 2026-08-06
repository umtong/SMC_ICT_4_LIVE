# Candidate 09 reproducible evaluation

- Status: **GATE_FAIL**
- Gate passed: **False**
- Baseline pooled daily geometric return: **-2.132806%**
- Baseline pooled NAV multiple across sampled days: **0.635887x**
- Baseline trades: **24**
- Maximum sampled-segment drawdown: **33.728318%**

## Fixed-week results

| week | return | daily geo | trades | win rate | PF | max DD | reversal | continuation | implementation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| week-a | -24.6844% | -3.9688% | 19 | 21.05% | 0.404 | 33.7283% | 14 | 5 | IMPLEMENTATION_ERROR |
| week-b | -6.4336% | -0.9455% | 2 | 0.00% | 0.000 | 6.4336% | 1 | 1 | IMPLEMENTATION_ERROR |
| week-c | -9.7650% | -1.4572% | 3 | 0.00% | 0.000 | 9.7650% | 2 | 1 | IMPLEMENTATION_ERROR |

## Gate checks

- FAIL — `implementation_ok`
- FAIL — `pooled_daily_geometric_return`
- FAIL — `minimum_trades_each_week`
- FAIL — `all_weeks_positive`
- FAIL — `profit_not_single_trade_dominated`

## Failure classification / structural diagnosis

- Classification: **IMPLEMENTATION_ERROR**
- Largest influence: **implementation contract**
- Required action: Fix execution/accounting/time contracts and rerun the identical weeks before changing logic.

## Known failure conditions

1. The public kline archive supplies taker-buy flow but not historical L2 replenishment/cancellation; hidden absorption and spoofing can be misclassified.
2. Nautilus bar execution uses adaptive OHLC ordering when both protective prices occur in one minute; trade-tick replay can change those fills.
3. Slippage, impact and funding reserve are charged as an explicit cash-equivalent composite cost; nonlinear capacity impact is not inferred from one-minute bars.
4. The test instrument's static margin model does not reproduce Binance notional tiers or every liquidation rule. Any rejected order is reported, never silently resized.
5. Continuation is skipped when no already-observable opposing liquidity pool exists; price-discovery trends can be missed by design.
6. A gate pass is not a final success: the frozen three-year BTC evaluation must also exceed the cost-after 1% daily geometric criterion without concentration.
