# Candidate 57 — MBE source-recross fresh v1 freeze

This policy-fresh experiment is frozen before the consumed lifecycle forensic
finishes. It runs only if
`evidence/mbe-lifecycle-forensic-v1/analysis.json` reports
`MBE_SOURCE_RECROSS_INVALIDATION_SUPPORTED_FRESH_REQUIRED`.

## Frozen candidate

The public MBE2 entry, at-least-two collision rule, source score, symbol
arbitration, stop, ROI ladder, costs, fills and current-NAV 3% risk sizing are
unchanged. The candidate adds one logical invalidation on a completed five-
minute boundary:

1. trade age is at least the **earliest source ROI horizon** independently
   supported in both consumed months;
2. estimated after-cost R is non-positive;
3. the entry symbol RSI is back at or above the source overbought boundary 70;
4. the entry symbol TEMA slope is positive.

This is the exact reversal of the short entry transition: the source entered
when RSI crossed below 70 and TEMA was falling above its middle band. Once the
minimum source-defined age has passed, a non-progressing trade whose RSI and
TEMA have both reversed is invalidated. The rule has no price-fit threshold,
no breadth threshold, no symbol exception and no outcome score.

## Fresh interval

The scored interval is **2024-04-01 through 2024-04-30 UTC**. A branch-wide
search for `2024-04` returned no existing candidate-57 experiment or result at
the time this freeze was committed. It was selected by the deterministic rule
“next complete unused month after the consumed March 2024 lifecycle window.”

Two otherwise identical one-slot NautilusTrader accounts are compared:

- `source_control`: lifecycle observer active, recross exit disabled;
- `source_recross`: same account with the single recross invalidation enabled.

## Predeclared causal predictions

- Recross exits must occur and primarily replace later negative exits.
- Paired affected trades must improve in R; aggregate improvement cannot come
  mainly from a newly exposed slot outlier.
- At least 80% of control ROI winners must remain positive, and the best control
  winner must remain positive.
- Expectancy, profit factor and geometric daily growth must all improve versus
  control; simply reducing trades or drawdown is insufficient.
- If the candidate improves one metric while damaging the winner engine, or if
  fresh April contains no causal recross events, the repair is rejected without
  changing the horizon or state rule.

The short-window project target additionally requires at least 30 completed
independent trades, after-cost geometric daily growth of at least 1%, positive
expectancy, PF above 1, MDD no greater than 20%, positive equity and all account
mechanics. A component-level success authorizes integration research only; it
does not authorize production or long evaluation by itself.
