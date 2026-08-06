# Candidate 06 AFHR Research Ledger

## Decision status before Nautilus execution

- Pure causal-logic tests in the patch workspace: **PASS**
- NautilusTrader campaign: **pending branch execution**
- Classification until execution: **implementation not yet verified in the fixed runtime; no performance claim**
- Existing candidate-06 data loading, delayed entry, order/fill handling, fees, one-tick slippage, positions, 3% NAV risk sizing and NAV accounting remain unchanged.

## Why the parent HML candidate failed

The selected HML full-response contract passed the first BTC week at **1.024347% geometric NAV growth per day**, 10 trades, 70% win rate and profit factor 1.806. It failed unchanged holdouts: week 2 was **-4.798367% per day** with 19 trades and 21.05% win rate; week 3 was **-0.413227% per day** with 2 trades.

This was not one undifferentiated failure.

1. **HTF acceptance quality was too weak.** A merely threshold-qualified auction could establish the same directional inventory state as an exceptional displacement.
2. **Boundary survival was mistaken for fresh information.** A bias could remain structurally alive after completed closes stopped making new direction-consistent extremes.
3. **Trade-count dependence was hidden.** In frozen week 2, one bearish HTF context produced 13 of 19 trades and only two wins.

## What worked and is retained

- Completed-bar causality and sealed validation weeks.
- Separation of pattern detectors from the scenario state machine.
- Signed-flow confirmation at HTF acceptance and at the later response stage.
- Confirmed 5-minute swing and equal-high/equal-low pools.
- One-use pool consumption.
- A separate full-sweep response rather than same-bar self-confirmation.
- Structural stop and nearest valid liquidity objective.
- One-bar delayed entry and favorable-drift guard.
- NautilusTrader orders, fills, positions, fees and portfolio NAV.

## AFHR hypothesis

```text
baseline-qualified completed 60m acceptance
→ range and volume are also exceptional versus sealed prior completed auctions
→ directional body confirms that displacement was not only a wick
→ context remains active only while completed closes refresh the directional extreme
→ confirmed 5m swing/equal liquidity is swept and reclaimed
→ a separate 1m bar breaks the full sweep structure with aligned response flow
→ unchanged structural stop and objective
→ delayed NautilusTrader entry
```

The current HTF auction is never included in its own range or volume reference distribution. A freshness refresh requires a new completed source-bar close in the accepted direction; an intrabar wick alone cannot refresh it.

## Controlled ablations

Fixed ex-ante order:

1. `afhr_full`
2. `afhr_quality_only_ablation`
3. `afhr_freshness_only_ablation`
4. `afhr_parent_hml_reference` — known-failed parent reference, ineligible for selection

Only adaptive HTF quality and directional-extreme freshness change. Pool construction, response logic, risk, costs, stop, target and execution remain fixed.

## Context-dependence diagnostic

The campaign records distinct accepted bias contexts, trades per context, the largest context share and unresolved mappings. These are causal diagnostics, not arbitrary first-week hard gates. A concentrated candidate may still be inspected rather than being discarded mechanically, but concentration must be considered before interpreting trade count as independent evidence.

## DLVR closure

DLVR was evaluated after two implementation errors were corrected under variable control: Binance depth timestamp parsing and payload transport/checksum. With 20,138/20,138 depth records accepted, full DLVR produced **-3.116% geometric NAV growth per day**, 11 trades, 18.18% win rate, profit factor 0.179 and 22.12% maximum drawdown. Removing depth confirmation worsened the result to **-8.326% per day** with 22 trades and 45.58% drawdown. Thus passive-depth confirmation reduced false price signals, but did not supply standalone directional alpha; DLVR was discarded as an independent scenario and retained only as a diagnostic primitive.


## Implementation-control note: quality diagnostic state chain

The first remote AFHR matrix reached NautilusTrader but stopped before any adaptive-quality performance result because a newly created diagnostic scenario declared `BASELINE_HTF_ACCEPTANCE` as its previous state. The repository recorder correctly requires every new scenario id to begin at `IDLE`. This was an event-serialization contract error, not a change in the AFHR market hypothesis. The fix changes only the diagnostic transition to `IDLE -> RESET` and retains `BASELINE_HTF_ACCEPTANCE` as evidence metadata. The same frozen first week and all strategy variables must therefore be rerun unchanged.
