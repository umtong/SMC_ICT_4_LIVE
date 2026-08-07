# Candidate 10 v24 — Spot–Perpetual Auction Reconciliation

## 1. Why this is a new lineage

v21–v23 treated a futures liquidation auction as the primary causal object.
The clean v23 result showed that OI clearing/building is useful for describing
participant state but did not produce positive directional expectancy. v24 does
not retune that detector. It discards the liquidation-auction lineage and asks a
different question:

> When Binance BTCUSDT spot and USD-M perpetual prices temporarily disagree,
> which venue is transmitting executed information and which venue is merely
> lagging or overshooting?

The traded instrument remains `BTCUSDT-PERP.BINANCE`; spot is an external causal
observation only. NautilusTrader continues to own all futures fills, positions,
commissions and raw NAV.

## 2. Research basis and bounded inference

The literature does not support a permanent rule that spot always leads or that
futures always lead.

- Frino, Gaudiosi, Webb and Zhou, *Price Discovery in Bitcoin Spot or Futures?
  The Jury Is Out* (Journal of Futures Markets, 2025, DOI
  `10.1002/fut.22560`) finds that conclusions depend on sampling frequency,
  model window, contract and spot venue; at one-second frequency futures
  generally lead, but leadership fluctuates by day.
- Cagli and Mandaci, *Information transmission between bitcoin derivatives and
  spot markets* (Economics and Business Letters, 2021, DOI
  `10.17811/ebl.10.4.2021.394-402`) reports bidirectional/high-frequency
  information transmission with structural shifts.
- Cont, Kukanov and Stoikov, *The Price Impact of Order Book Events*
  (arXiv:1011.6402) links short-horizon price movement more directly to net
  order-flow imbalance and depth than to volume alone.
- Lim and Gorse, *Deep Recurrent Modelling of Stationary Bitcoin Price
  Formation Using the Order Flow* (2020) shows that order-flow representations
  can be more temporally stable than raw price-book snapshots.

The inference used by v24 is therefore conditional, not universal: a temporary
basis displacement becomes tradable only when completed price and executed-flow
states identify either lagging reconciliation or unconfirmed overshoot.

Binance's official public-data repository defines spot and USD-M futures
`aggTrades` columns and supplies SHA256 `.CHECKSUM` files. Every archive used by
v24 is checked against that official checksum before parsing.

## 3. SMC/ICT bridge without discretionary pattern naming

This is a programmable intermarket-divergence scenario rather than a candle
pattern.

```text
cross-market displacement
→ identify which auction moved with executed aggressor flow
→ identify whether the other auction accepted, lagged, or failed to confirm
→ require a later completed reconciliation state
→ enter only on the first later raw perpetual trade
→ invalidate beyond the event extreme
→ target the fair-basis price fixed at event detection
```

The detector only identifies disagreement. The scenario state machine decides
whether that disagreement is spot-led continuation/catch-up or perpetual-led
overshoot/reversion.

## 4. Causal data object

Spot and perpetual raw aggregate trades are independently aggregated into
completed five-second rows.

For each venue and interval `[t, t+5s)` the row contains:

- OHLC from actual aggregate trades;
- quote notional;
- taker-buy quote notional;
- signed executed-flow imbalance;
- trade count;
- first and last raw trade timestamp.

A row is stamped at `t+5s`; every source trade must be strictly earlier than the
row timestamp. Only timestamps present in both venues are aligned. The first
strategy action based on a row occurs on a raw perpetual `TradeTick` strictly
later than the row timestamp.

The causal fair basis is the median of prior completed log
`perpetual/spot` basis observations. Its dispersion, return scales, flow scales,
perpetual range and liquidity notional are also estimated from prior rows only.
The current event is never included in its own baseline.

## 5. Full state grammar

### 5.1 Spot-led perpetual catch-up

```text
prior fair-basis regime
→ completed spot displacement ≥ 2.5 robust deviations
→ spot executed flow agrees with the displacement
→ perpetual moves in the same direction but by no more than 65% of spot move
→ basis deviates ≥ 2 robust deviations in the lagging-perpetual direction
→ create one SPOT_LEAD_CATCHUP event
→ fix fair target = event spot close × exp(prior fair basis)
→ wait at most 12 completed five-second rows
→ spot does not materially reverse
→ basis contracts by at least 20% from event deviation
→ perpetual executed flow turns in the catch-up direction
→ first later raw perpetual trade enters toward the fixed fair target
```

### 5.2 Perpetual overshoot reversion

```text
prior fair-basis regime
→ completed perpetual displacement ≥ 2.5 robust deviations
→ perpetual executed flow agrees with the displacement
→ spot price and spot executed flow do not confirm at comparable strength
→ basis deviates ≥ 2 robust deviations in the perpetual-move direction
→ create one PERP_OVERSHOOT_REVERSION event
→ fix fair target = event spot close × exp(prior fair basis)
→ wait at most 12 completed five-second rows
→ basis contracts by at least 20%
→ perpetual executed flow reverses
→ spot still does not confirm the original perpetual move
→ first later raw perpetual trade enters back toward the fixed fair target
```

A probe that reaches its fair target before confirmation is recorded as
`CONVERGED_WITHOUT_ENTRY`. A probe that does not reconcile within one minute
expires. After every terminal outcome, no new event is allowed until basis and
both return states normalize for three completed rows.

## 6. Entry, target, invalidation and costs

The target is fixed at event detection from the event spot close and the fair
basis estimated strictly before the event. It is not rewritten using future spot
prices.

The stop is beyond the actual perpetual event extreme plus one causal median
five-second range. The plan is rejected before order creation unless its net
reward/risk remains at least 1.35 after:

- entry and stop/target taker fees;
- the existing size-dependent square-root impact estimate;
- execution reserve ticks.

Quantity is solved from exactly 3% of current whole-account all-cost NAV. Modeled
impact that Nautilus does not post to the exchange account is debited at actual
entry and exit fills; every later risk budget uses raw Nautilus NAV minus all
prior modeled debits. No nominal cap, score multiplier or arbitrary leverage
limit is added.

## 7. One-event identity

One cross-market dislocation can create at most one probe and one plan. It
cannot be split by every five-second observation. Event identity is released
only after basis and both return states normalize, preventing one information
shock from being counted as many independent trades.

Reports retain 1-, 5- and 15-minute PnL-cluster concentration in addition to
scenario IDs.

## 8. Exact ablation

`ablation-price-basis-perp-flow-only` removes only spot executed-flow
information.

Full and ablation share exactly the same:

- spot and perpetual prices and timestamps;
- prior fair basis and all price-based dislocation conditions;
- perpetual executed flow;
- event identity, confirmation order and expiry;
- target fixed at detection and structural stop;
- first-later-perpetual-trade entry;
- fees, fill model, size-dependent impact and live conservative ledger;
- whole-account current-NAV 3% risk;
- seed, BTC week and NautilusTrader engine.

If the ablation is equal or superior, spot aggressor flow has not supplied useful
incremental causal information in this grammar.

## 9. First-week falsification

v24 is a clean logic failure and is discarded without threshold tuning when:

- gross or all-cost expectancy is materially negative with enough independent
  events;
- the full variant adds no coherent improvement over the exact ablation;
- all opportunities are filtered out by causal confirmation or executable
  cost geometry;
- profit is dominated by one event cluster;
- either mode repeatedly reaches the structural stop before approaching the
  fixed fair target;
- success requires selected hours, hand-picked news exclusions, altered weeks,
  symbol-specific constants or relaxed costs.

Only a clean first BTC week with repeated positive all-cost outcomes unlocks the
other two preselected BTC weeks. One good trade or damage reduction alone is not
promotion.
