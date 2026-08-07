# BAVR Research Ledger

## Predeclared thesis

A market which repeatedly trades substantial volume around the same prices is a
balanced auction rather than an active directional discovery process.  In the
immediately following auction, an aggressive excursion beyond that completed
value area is not faded merely because it crossed an edge.  Reversion becomes
tradable only when outside acceptance fails, price closes back into value, and a
separate completed response continues toward the prior accepted point of
control.

This is not a renamed liquidity-sweep strategy.  The source level and objective
come from checksum-verified transaction volume at price, not adjacent candle
highs/lows, and the balance state is formed before the excursion.

## State order

```text
COMPLETED PROFILE A
-> COMPLETED PROFILE B
-> SHARED VALUE / BALANCE ASSESSMENT
-> NEXT-AUCTION VALUE-EDGE EXCURSION
-> OUTSIDE ACCEPTANCE TEST
-> VALUE RECLAIM
-> SEPARATE ROTATION RESPONSE
-> ENTRY
-> POC OR OPPOSITE VALUE EDGE
```

Sustained closes with continuing same-direction aggressive flow outside value
classify price discovery and reset the reversion scenario.

## Data contract

- Binance public USD-M `aggTrades`, daily checksum verified.
- Buyer-maker trades are seller aggressive; non-buyer-maker trades are buyer aggressive.
- Trades are streamed into completed one-minute signed-flow summaries and
  completed 15-minute volume-at-price profiles.
- The 70% value area expands contiguously from the highest-volume price.
- A profile completing at time `t` cannot seed a decision on the same bar; the
  earliest tradable observation is after `t`.
- Raw trade data is context only.  NautilusTrader owns all event replay, order,
  fill, position, commission and NAV accounting.

## Balance contract

The full mechanism requires adjacent completed profiles to:

- overlap by at least half of the smaller value-area width;
- contain each other's POC inside value;
- have bounded directional efficiency;
- have bounded aggregate signed-flow imbalance;
- retain non-trivial volume tails on both sides;
- avoid single-price volume concentration.

These are a fixed state definition, not a search grid.

## Fixed first-week matrix

1. `bavr_balanced_value_full` — full balance, transaction distribution and
   aggTrade flow.  Only selectable variant.
2. `bavr_without_distribution_ablation` — remove only two-sided distribution
   quality.
3. `bavr_without_balance_ablation` — remove only the adjacent-auction balance
   requirement.
4. `bavr_kline_flow_reference` — replace transaction-signed one-minute flow with
   the existing kline taker-flow proxy.

The ablations cannot be selected from this campaign.  They determine whether a
failure is caused by opportunity suppression, absence of balance information,
or absence of trade-level flow information.

## Fixed execution contract

- first frozen BTC week: 2024-02-26 UTC;
- subsequent frozen weeks only after complete first-week gate;
- NautilusTrader 1.230.0 only;
- current whole-account NAV and three-percent planned-loss budget;
- no score-based risk multiplier or arbitrary notional/leverage cap;
- one global pending-new-entry or open-position slot;
- existing effective fees, one-tick slippage and fill model;
- structural stop outside the excursion extreme;
- target is the nearest still-unreached POC, otherwise opposite value edge;
- current response bar cannot have already touched the selected objective.

## Failure classification

- Archive, checksum, parser, timestamp, profile, injection, Nautilus API or
  output-contract failure: implementation/data failure; repair only that defect
  and rerun the identical week.
- Valid metrics but full first-week gate failure: logic failure.  Inspect the
  fixed ablations once, record the largest performance factor, and discard if no
  structural path exists.
- First-week pass followed by sealed-week failure: unchanged generalization
  failure; no first-week rescue tuning.
