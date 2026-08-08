# Candidate 16 v2 — Displayed Liquidity Defense → Later Initiative

Candidate 16 v1 proved that high effort, low progress, and a fast reclaim do not
by themselves identify a tradable failed auction. v2 preserves the existing
NautilusTrader/data path and changes only the state ownership of the trade.

```text
external liquidity interaction
→ attack effort
→ queue/depth response
   ├─ defense + reclaim → freeze failure, no order
   │                      → later price/flow/book initiative → reversal
   ├─ withdrawal + outside residence → first defended retest → continuation
   └─ disagreement → UNRESOLVED / NO TRADE
```

The design follows market-microstructure findings that short-horizon price impact
is driven by order-flow imbalance including limit additions/cancellations and is
scaled by available depth, plus practitioner failed-breakout logic based on
replenishment, non-persistent velocity, and a later failed attempt. It explicitly
does not treat a single CVD/flow or candle pattern as sufficient state evidence.

The first v2 screen is frozen in `PRE_REGISTRATION_V2.md`. `V1_FAILURE_ANALYSIS.md`
contains the parent failure attribution.
