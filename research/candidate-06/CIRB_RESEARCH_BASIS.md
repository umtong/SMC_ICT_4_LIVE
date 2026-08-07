# Candidate 06 v6.0 — Crowding Inventory-Response Bifurcation

## Market mechanism

An open-interest contraction is not itself a direction signal. It says gross
inventory disappeared. The causal question is what replaced the positions which
were closed.

The existing OIDB experiment showed that the same completed OI-contraction,
price and taker-flow shock can lead to two different mechanisms. A completed
all-account long/short ratio provides a direct composition observation at the
same five-minute event timestamp:

```text
extreme completed OI contraction
+ directional five-minute price move
+ aligned completed taker flow
→ compare completed all-account composition with its immediately prior value
```

The sign creates two state branches without a fitted magnitude threshold.

### DISCHARGE

```text
account composition moves in the price-shock direction
→ the displaced-side inventory is actually being closed
→ later completed response may:
   A. reclaim the shock and reverse toward pre-shock liquidity; or
   B. keep contracting OI and extend price discovery
```

### COUNTER_INVENTORY

```text
account composition moves against the price shock
→ new opposing inventory is replacing the liquidated positions
→ reversal is forbidden
→ continuation is possible only after a later completed metrics observation:
   A. OI rebuilds a fixed fraction of the original contraction;
   B. opposing account composition remains;
   C. price and taker flow extend in the original shock direction
→ the replacement inventory is trapped
```

The initiating event cannot trade. Every entry needs a separate completed
response. Robust z-scores use only earlier observations and are diagnostic only;
the causal branch uses the raw sign of the completed composition change.

## Why this is not a fitted filter

The branch was derived from a causal audit of the already executed OIDB trades,
not by maximizing a new threshold. In the prior evidence, 9 of 10 winning OIDB
trades had all-account composition move in the shock direction, while both
sealed-week losses moved against it. The new system does not merely reject the
opposite observations. It assigns them a different economic mechanism and
requires later OI rebuilding before a same-direction continuation can exist.

## Fixed execution contract

- BTCUSDT perpetual only during staged discovery.
- Binance public USD-M one-minute klines and completed five-minute metrics with
  checksum manifests.
- One native NautilusTrader account, one global position/order slot.
- Same completed-bar signal-close submission contract as OIDB.
- Fill model, seven-basis-point effective fee per fill, one-tick adverse
  slippage, structural bracket, and all OIDB stop/target rules unchanged.
- Planned loss remains three percent of whole-account NAV per approved trade.
- No nominal cap, leverage cap, score multiplier or post-hoc session filter.

## Predeclared matrix

1. `cirb_full_bifurcation`: both causal branches; only selectable candidate.
2. `cirb_discharge_only`: attribution of true-discharge responses.
3. `cirb_counter_inventory_only`: attribution of trapped replacement inventory.
4. `cirb_without_account_composition_ablation`: identical legacy OIDB response
   with the causal composition split removed; never selectable.

The full mechanism must pass the existing first BTC week gate. Only then is the
configuration locked and the two frozen BTC weeks opened. Long evaluation is
not authorized unless all three weeks pass unchanged.
