# Candidate 09 v1 — discarded complete candidate

Reproducible final v1 commit: `dc717478d0da4a0b699e6aef35f483aff02f2464`.

The implementation contract was repaired without changing the three frozen BTC weeks or strategy thresholds. Native account reconciliation then reached floating-point zero error. The causal local-pivot liquidity engine nevertheless failed all three weeks after composite costs:

- pooled daily geometric return: **-1.753871%**
- pooled NAV multiple over 21 sampled days: **0.689643x**
- trades: **24**
- week returns: **-19.6894%, -5.9111%, -8.7331%**
- maximum segment drawdown: **29.8281%**

Single-variable ablations (`no-flow`, `no-reclaim-confirmation`, `no-acceptance-confirmation`) all performed worse. This separates the failure from implementation defects and shows that flow/reclaim/acceptance checks reduced damage but did not create positive conditional edge. The complete v1 was classified `LOGIC_ERROR_NO_STRUCTURAL_PATH` and discarded.

Known v1 failure condition: treating every causal one-minute pivot as an external liquidity pool generated low-quality breaches, especially reversal entries without a genuine micro-structure shift.
