# Candidate 57 — MBE collision topology fresh v1 freeze

This experiment follows the completed April 2026 MBE-only account, where the
unchanged public MBE2 short RSI/TEMA cross produced 34 trades, 26 wins, PF
1.3818 and positive expectancy. The source ROI engine was strong, but the
account still grew only 0.0754% geometrically per day and a small set of
structural-stop episodes consumed most gains.

The consumed trade ledger suggested a causal distinction that was not part of
the source indicator:

- exactly two simultaneously actionable symbols behaved like a partial/local
  exhaustion event;
- three or more simultaneously actionable symbols behaved more like a broad
  market move whose continuation can overwhelm a short mean-reversion entry.

This is a **state partition**, not a parameter optimization. The per-symbol
signal, completed 5m candles, RSI14/TEMA9/SMA20 logic, effective leverage,
stop, ROI ladder, fees, latency, sizing and accounting remain identical.

## Frozen cells

1. `ge2_control` — existing policy: trade when at least two symbols signal.
2. `exact2` — trade only when exactly two symbols signal.
3. `ge3plus` — contrast cell: trade only when at least three symbols signal.

Within an accepted boundary, source score and BTC/ETH/SOL/XRP priority remain
unchanged. No price threshold, volume rule, symbol exception, direction rule,
time filter, outcome score or post-result tuning is allowed.

## Mechanical equivalence requirement

Before the fresh interval is evaluated, the finite-history implementation must
replay the already-consumed April 2026 `ge2_control` and reproduce the committed
full-history MBE account’s completed episode keys, trade count, ending NAV and
family outcome within numerical tolerance. A parity failure is an
implementation error and blocks all alpha conclusions.

## Fresh interval

The scored interval is **2024-03-01 through 2024-03-31 UTC**. A branch-wide
search for `2024-03` and `2024-03-01` returned no existing result or frozen
experiment. The month was selected before replay by the deterministic rule
“earliest complete month after the February Jump/Ichi window absent from the
branch evidence search.”

## Predeclared causal predictions

If the topology interpretation is correct:

- `exact2` must improve expectancy, profit factor and geometric daily growth
  versus `ge2_control`, not merely reduce trades;
- `ge3plus` must have lower expectancy or profit factor than `exact2` and a
  higher share of structural-stop/bracket losses;
- the positive ROI-exit engine must remain present in `exact2`;
- no single newly exposed outlier may explain the improvement;
- exact2 should retain enough independent opportunities to be useful as a
  component rather than becoming a rare filter.

If `exact2` only removes both winners and losers proportionally, if `ge3plus`
is equally strong, or if the result depends on one outlier, the hypothesis is
rejected without changing the collision count rule.

A positive component result authorizes consideration inside a one-slot N→1
account. It does not authorize long evaluation or production by itself.
