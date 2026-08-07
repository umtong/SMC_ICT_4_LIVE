# Candidate 09 reproducible evaluation

- Status: **GATE_FAIL**
- Gate passed: **False**
- Baseline pooled daily geometric return: **-0.144956%**
- Baseline pooled NAV multiple across sampled days: **0.969996x**
- Baseline trades: **1**
- Maximum sampled-segment drawdown: **3.000354%**

## Fixed-week results

| week | return | daily geo | trades | win rate | PF | max DD | reversal | continuation | implementation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| week-a | -3.0004% | -0.4342% | 1 | 0.00% | 0.000 | 3.0004% | 0 | 1 | OK |
| week-b | 0.0000% | 0.0000% | 0 | n/a | n/a | 0.0000% | 0 | 0 | OK |
| week-c | 0.0000% | 0.0000% | 0 | n/a | n/a | 0.0000% | 0 | 0 | OK |

## Gate checks

- PASS — `implementation_ok`
- PASS — `account_remained_recoverable`
- FAIL — `pooled_daily_geometric_return`
- FAIL — `minimum_total_trades`
- FAIL — `minimum_active_weeks`
- PASS — `profit_not_single_trade_dominated`

## Failure classification / structural diagnosis

- Classification: **LOGIC_ERROR_NO_STRUCTURAL_PATH**
- Largest influence: **insufficient cost-after conditional edge or opportunity rate**
- Required action: Discard candidate-09 as a complete candidate; preserve only the listed mechanisms for later hypotheses.
- Parts worth preserving: OI cascade continuation branch produced executable events; risk-budgeted loss path remained recoverable in the gate sample

## Known failure conditions

1. Five-minute OI is a positioning snapshot, not a trader-level liquidation label; the baseline infers position reduction only when OI, price and taker flow agree.
2. Nautilus bar execution uses adaptive OHLC ordering when both protective prices occur in one minute; trade-tick replay can change those fills.
3. Slippage, impact and funding reserve are charged as an explicit cash-equivalent composite cost; nonlinear capacity impact is not inferred from one-minute bars.
4. The test instrument's static margin model does not reproduce Binance notional tiers or every liquidation rule. Any rejected order is reported, never silently resized.
5. Metrics are exposed one completed minute after create_time; the baseline therefore demands a new completed bar after availability and will miss cascades that finish earlier.
6. A gate pass is not a final success: the frozen three-year BTC evaluation must also exceed the cost-after 1% daily geometric criterion without concentration.
