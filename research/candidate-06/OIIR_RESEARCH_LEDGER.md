# OIIR Research Ledger

## Hypothesis

A directional move with extreme completed OI expansion is newly opened
inventory. The same move with extreme OI contraction is deleveraging. They
must not share one generic continuation/reversal rule.

```text
prior-only positive/negative OI-change distributions
→ completed extreme OI expansion or contraction + aligned price/taker flow
→ no event-bar entry
→ BUILD: later OI retention → first opposing-flow pullback holds value
          → separate resumption
→ UNWIND: later OI keeps contracting → continuation
           OR OI re-expands + opposite reclaim → counter-inventory reversal
→ structural stop/objective
→ NautilusTrader orders, fills, fees, positions and NAV
```

## Fixed matrix

1. Full OI inventory-regime relay — eligible.
2. New-inventory BUILD branch only — attribution.
3. Full system without counter-inventory rebuild for reversal — one
   core-variable ablation.

The first BTC week is 2024-02-26. Only the unchanged full configuration may
advance to 2024-09-23 and 2024-04-22. Long evaluation is forbidden unless all
three weekly gates pass.

## Predecessor evidence used

OIDB demonstrated that OI is causal rather than cosmetic: its first week passed
at 1.3481% geometric growth per day with 14 trades and 10 wins, while removing
OI produced -8.4651% per day and 46.16% drawdown. OIDB was nevertheless
discarded because locked week 2 produced two losses and -0.4674% per day.
Locked week 3 reached 0.9327% per day but still failed the fixed 1% growth gate.

The new hypothesis therefore preserves completed OI information but replaces
price-only deleveraging reversal with completed counter-inventory rebuild and
adds the distinct fresh-inventory retention path.

## Fixed contracts

NautilusTrader 1.230.0 only; current total NAV; 3% planned loss per approved
trade; explicit fees and one-tick adverse slippage; one global pending-entry or
position slot; no score-based sizing, arbitrary notional cap, post-result
threshold rescue, or custom backtest engine.
