# Understanding contract

## Complete decision object

A valid trade plan must answer, before submission:

1. What higher-timeframe context exists and when did it become observable?
2. Which price area is the expected liquidity/objective interaction?
3. Did price reject that area, become accepted beyond it, or remain unresolved?
4. What later observation confirms the state without reusing the interaction candle as its own proof?
5. What exact price will be entered?
6. Which price falsifies the causal episode?
7. Which opposing objective already existed before the episode began?
8. Is the pre-cost geometry at least 1.0R?
9. Is the four-symbol account slot still free?

Missing answers produce `NO TRADE`, not a default parameter.

## Rejection sequence

```text
fresh 60m/15m context
→ 5m excursion outside far edge
→ full reclaim (partial reclaim remains unresolved)
→ later event-local bullish/bearish OB or FVG
→ first later retest and reaction
→ stop outside sweep extreme
→ nearest unspent opposing structure observed before the sweep
```

## Acceptance sequence

```text
fresh 60m/15m context
→ 15m body close outside
→ next 15m opens and closes outside
→ later 5m retest from outside which preserves the S/R flip
→ stop beyond the opposite context edge
→ nearest unspent opposing structure observed before the break
```

## Error separation

- Source fidelity: did the code reproduce the decision sequence supported by the material?
- Decision fidelity: with the same information timestamp, did code select the same context/path as the human case?
- Execution fidelity: did NautilusTrader submit and fill the intended order with realistic costs and ordering?
- Economic validity: after the first three are sound, does the integrated continuous account compound after costs?

A failed economic result does not by itself prove that OB/FVG or Fakeout has no value. The state trace and multi-timeframe trade window identify whether the failure came from context selection, state classification, entry geometry, objective selection or execution.
