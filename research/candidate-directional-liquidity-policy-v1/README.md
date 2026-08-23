# Directional Liquidity Policy v1

A unified causal day-trading policy. Direction is represented by the next meaningful unswept liquidity pool and by whether the current auction has failed or been accepted. Trendlines/channels are context, OB/FVG/BPR are entry-location geometry, and price/volume/derivatives flow determines ownership. The policy never treats those tools as independent strategies.

The research path is:

```
hierarchical liquidity ledger
-> failed/accepted auction and active delivery route
-> directional route target/invalidation
-> fresh displacement location
-> first controlled return and completed response
-> one immutable full-exit action
-> blocked-period route/entry learning
-> one four-symbol continuous account
```

No symbol identity, calendar period, or future path field is available to either model. Future bars are used only for route and action first-passage labels.
