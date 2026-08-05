# Live parity design

Candidate 01 keeps the decision core free of backtest APIs so the tested state sequence can run unchanged in live trading.

## Shared components

The following code is identical in research and live modes:

- `AuctionBar` validation and signed aggressive-flow calculation;
- completed-range construction;
- rejection/acceptance state transitions;
- event/observation timestamps;
- trade-plan entry, target, stop, and expiry logic;
- global ownership semantics: one pending new entry or open position across all allowed instruments.

## Live adapter responsibilities

A production NautilusTrader node must:

1. subscribe to BTCUSDT, ETHUSDT, SOLUSDT, and XRPUSDT one-minute completed klines plus current instrument/account state;
2. construct `AuctionBar` only after the exchange minute closes;
3. load exchange instrument metadata, leverage bracket, margin requirements, fee tier, and funding schedule instead of using research defaults;
4. keep one persistent `GlobalEntryGate` for all four instruments;
5. calculate quantity from total account NAV and the exact expected-loss equation;
6. submit market entry with contingent stop-market protection and target through the venue adapter;
7. block new entries from order submission until the position and all opening orders are conclusively gone;
8. reconcile open orders, positions, and gate owner at startup and after every disconnect;
9. flatten and halt on missing protection, duplicated ownership, stale data, or reconciliation mismatch;
10. emit the same scenario and execution event schema as research.

## Multi-instrument scheduler

The project constraint is global, not per symbol. The live process should evaluate all four completed bars at a timestamp, collect valid trade plans, and choose at most one using a deterministic, scenario-native tie-break:

```text
1. earliest observed_time_ns
2. larger net reward/risk after current costs
3. lexicographic instrument id (only as final deterministic tie-break)
```

The tie-break does not change quantity and is not a model-score risk multiplier. All non-selected plans expire; they are not queued behind an existing position.

## Restart invariant

```text
count(open positions across four instruments)
+ count(working orders capable of increasing exposure)
<= 1
```

Reduce-only stop, target, and flatten orders do not count as new entries. If the exchange state cannot prove this invariant, the node may only cancel or reduce exposure.

## Research-to-live differences that must remain explicit

- Research uses one-minute bar execution; live receives actual order acknowledgements and fills.
- Research folds fee, slippage, impact, and possible funding into a fixed 7 bps/side stress; live uses account-specific fee and real-time execution estimates.
- Research uses a declared BTC margin model with liquidation enabled; live uses the exchange's current notional bracket and liquidation mechanics.
- Research has no depth curve; live must reject orders whose expected impact invalidates the per-unit loss budget rather than impose an unrelated notional cap.

A live adapter is not approved solely because code imports successfully. It must pass replay parity, sandbox/order-event fault tests, restart reconciliation tests, and a shadow-trading period with identical scenario-event hashes.
