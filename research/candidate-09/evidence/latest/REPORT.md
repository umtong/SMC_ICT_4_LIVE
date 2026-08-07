# Candidate 09 reproducible evaluation

- Status: **FAILED_LONG_EVALUATION**
- Gate passed: **True**
- Baseline pooled daily geometric return: **1.387699%**
- Baseline pooled NAV multiple across sampled days: **1.335644x**
- Baseline trades: **15**
- Maximum sampled-segment drawdown: **5.910604%**

## Fixed-week results

| week | return | daily geo | trades | win rate | PF | max DD | reversal | continuation | implementation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| week-a | 20.2099% | 2.6644% | 8 | 62.50% | 2.850 | 2.9995% | 8 | 0 | OK |
| week-b | -3.0020% | -0.4345% | 1 | 0.00% | 0.000 | 3.0020% | 1 | 0 | OK |
| week-c | 14.5481% | 1.9593% | 6 | 50.00% | 2.427 | 5.9106% | 6 | 0 | OK |

## Gate checks

- PASS — `implementation_ok`
- PASS — `account_remained_recoverable`
- PASS — `pooled_daily_geometric_return`
- PASS — `minimum_total_trades`
- PASS — `minimum_active_weeks`
- PASS — `profit_not_single_trade_dominated`

## Known failure conditions

1. The public kline archive supplies taker-buy flow but not historical L2 replenishment/cancellation; hidden absorption and spoofing can be misclassified.
2. Nautilus bar execution uses adaptive OHLC ordering when both protective prices occur in one minute; trade-tick replay can change those fills.
3. Slippage, impact and funding reserve are charged as an explicit cash-equivalent composite cost; nonlinear capacity impact is not inferred from one-minute bars.
4. The test instrument's static margin model does not reproduce Binance notional tiers or every liquidation rule. Any rejected order is reported, never silently resized.
5. Continuation is skipped when no already-observable opposing liquidity pool exists; price-discovery trends can be missed by design.
6. A gate pass is not a final success: the frozen three-year BTC evaluation must also exceed the cost-after 1% daily geometric criterion without concentration.

## Frozen long evaluation

- Status: **FAIL**
- Daily geometric return: **-0.847774%**
- NAV multiple: **0.000089x**
- Trades: **663**
- Maximum drawdown: **99.991138%**
