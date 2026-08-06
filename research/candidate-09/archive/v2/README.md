# Candidate 09 v2 — complete candidate failed; continuation mechanism preserved

Reproducible final v2 result commit: `943c0bc202c36f233e7dbfd1783ff02362586e14`.

V2 replaced one-minute local pivots with completed four-hour auction ranges and achieved clean Nautilus execution/accounting on the same three frozen BTC weeks. It materially improved v1 but did not meet the project gate:

- pooled daily geometric return: **-0.173236%**
- pooled NAV multiple: **0.964244x**
- trades: **5**
- maximum segment drawdown: **3.000172%**
- week returns: **+1.8637%, -2.4120%, -3.0002%**

The complete candidate was rejected because opportunity rate was insufficient and results did not transfer across weeks. The single-variable ablations showed:

- removing acceptance produced **-1.268523% daily** and a **0.764836x** NAV multiple, so outside acceptance is essential;
- removing MSS produced 25 trades and **-0.082586% daily**, improving frequency but concentrating 19 trades and +9.35% in week-a while weeks b/c remained negative;
- removing flow produced the same sparse baseline outcome in this sample, so flow was not the bottleneck.

Valid component: the baseline continuation branch earned approximately **+2,013.72 USDT net across two trades**, while baseline reversal trades lost approximately **-5,562.18 USDT net**. V3 therefore preserves strict acceptance/retest continuation, removes reversal, and increases logically distinct opportunities through completed 60m/240m/1440m auction extremes rather than loosening acceptance.
