# Candidate-02 locked hypothesis

This file records the hypothesis and evaluation choices **before** candidate-02
reads any of the selected market weeks.

## Causal claim

A clustered external high or low represents resting stop liquidity. A finite
excursion through that pool can trigger a stop-order cascade. The excursion is
not a reversal signal by itself. A reversal becomes actionable only when all of
the following occur in order:

1. The external pool was observable before the excursion. Pools are confirmed
   five-minute pivots or completed six-hour auction-block extremes.
2. Price trades beyond the pool by a non-trivial, ATR-scaled amount.
3. A completed one-minute bar re-enters the prior auction quickly. Failure to
   reclaim is acceptance, not a fade.
4. Price displaces in the opposite direction through an internal swing that was
   already observable when the excursion began.
5. The displacement is directional, volume-supported and leaves a three-candle
   fair-value gap.
6. Price retests and rejects that imbalance before the sweep extreme is broken.
7. The nearest still-active opposing external pool offers at least the locked
   reward/risk geometry.

Entry is a market bracket after cross-asset arbitration. The sweep extreme plus
an ATR buffer is the causal invalidation. The nearest opposing external pool is
the target. A time exit prevents an intraday thesis becoming an unintended
swing position.

## Locked risk and execution assumptions

- Planned loss budget: current automatic-account NAV × 1.00%.
- Quantity: planned loss budget divided by stop distance plus entry/stop fees,
  slippage, impact and funding allowance per unit.
- No maximum notional cap, model-score multiplier or arbitrary leverage sizing
  rule is added.
- One pending entry bracket or one position across all allowed instruments.
- BTC USD-M perpetual one-minute bars are the first experimental market.
- NautilusTrader owns orders, fills, positions, commissions, margin, liquidation
  and account NAV.
- Bar data has no spread/order-book depth. Conservative slippage, impact and a
  funding allowance are charged through instrument commissions so headline NAV
  is cost-after rather than a frictionless side calculation.
- Adaptive OHLC execution ordering is enabled. Signal arbitration waits one
  completed minute, eliminating same-timestamp instrument ordering bias at the
  cost of conservative entry delay.

Exact values are in `config.json` and are not changed between the three screen
weeks.

## Predetermined BTC screen

Population: every Monday from 2022-01-03 through 2025-12-22 inclusive.
Selection: `random.Random(20260805).sample(population, 3)`.

| Role | UTC week start |
|---|---|
| discovery | 2024-07-08 |
| confirmation-a | 2025-08-04 |
| confirmation-b | 2022-08-22 |

Decision rule: run all three with identical logic and costs. Advance to longer
and four-instrument evaluation only if the combined cost-after NAV geometric
daily growth reaches 1% and the result is not isolated to one week or a handful
of trades. Otherwise reject or structurally redesign the scenario; do not tune
execution polish around weak alpha.
