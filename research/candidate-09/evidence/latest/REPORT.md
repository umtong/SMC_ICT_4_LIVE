# Candidate 09 reproducible evaluation

- Status: **GATE_FAIL**
- Gate passed: **False**
- Baseline pooled daily geometric return: **0.576532%**
- Baseline pooled NAV multiple across sampled days: **1.128314x**
- Baseline trades: **12**
- Maximum sampled-segment drawdown: **6.342884%**

## Fixed-week results

| week | return | daily geo | trades | win rate | PF | max DD | reversal | continuation | implementation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| week-a | 1.6440% | 0.2332% | 8 | 37.50% | 1.103 | 6.3429% | 8 | 0 | OK |
| week-b | 0.0000% | 0.0000% | 0 | n/a | n/a | 0.0000% | 0 | 0 | OK |
| week-c | 11.0064% | 1.5029% | 4 | 50.00% | 2.711 | 3.0002% | 4 | 0 | OK |

## Gate checks

- PASS — `implementation_ok`
- PASS — `account_remained_recoverable`
- FAIL — `pooled_daily_geometric_return`
- FAIL — `minimum_total_trades`
- FAIL — `minimum_active_weeks`
- FAIL — `profit_not_single_trade_dominated`

## Failure classification / structural diagnosis

- Classification: **LOGIC_ERROR_WITH_STRUCTURAL_PATH**
- Largest influence: **after-retest-only**
- Required action: The single-variable ablation after-retest-only improved pooled cost-after growth; revise only that confirmation layer, then freeze and retest.

## Known failure conditions

1. The public kline archive supplies taker-buy flow but not historical L2 replenishment/cancellation; hidden absorption and spoofing can be misclassified.
2. Nautilus bar execution uses adaptive OHLC ordering when both protective prices occur in one minute; trade-tick replay can change those fills.
3. Slippage, impact and funding reserve are charged as an explicit cash-equivalent composite cost; nonlinear capacity impact is not inferred from one-minute bars.
4. The test instrument's static margin model does not reproduce Binance notional tiers or every liquidation rule. Any rejected order is reported, never silently resized.
5. Continuation is skipped when no already-observable opposing liquidity pool exists; price-discovery trends can be missed by design.
6. A gate pass is not a final success: the frozen three-year BTC evaluation must also exceed the cost-after 1% daily geometric criterion without concentration.
