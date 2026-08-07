# OIDB Research Ledger

## Hypothesis

A completed extreme open-interest contraction with aligned price displacement and aggressive taker flow is a forced inventory shock. It is not traded immediately. A later completed response must classify the shock as either exhaustion/reclaim or continued deleveraging/price discovery.

## Predeclared state sequence

```text
prior-only OI-drop distribution
→ extreme completed 5m OI contraction + aligned price/flow
→ no same-bar entry
→ later completed exhaustion reclaim OR next completed persistent OI contraction
→ structural stop and objective
→ NautilusTrader bracket, fills, fees and NAV
```

## Fixed comparison

1. Full OIDB bifurcation — eligible.
2. Reversal branch only — branch attribution.
3. Remove OI contraction, retain price/flow and response — one core-variable ablation.

Only the full variant may advance. The first BTC week is 2024-02-26. Configuration is unchanged for 2024-09-23 and 2024-04-22. Long evaluation is forbidden unless all three gates pass.

## Error classification

- Missing/invalid metrics, timestamp mismatch, constructor/runner/order/NAV failure: implementation or data failure; repair only that error and rerun the same week.
- Valid Nautilus metrics but failed gate: logic failure. Use the predeclared no-OI ablation once; if it does not show a structural path, discard.

## Preserved project contracts

NautilusTrader 1.230.0 only; realistic fees and one-tick adverse slippage; current total NAV; 3% planned loss; one global new-order/position slot; no score-based risk multiplier or arbitrary notional cap.
