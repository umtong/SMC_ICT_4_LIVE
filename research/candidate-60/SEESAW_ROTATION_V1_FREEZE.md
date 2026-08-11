# Candidate 60 — frozen confirmed large-coin seesaw rotation diagnostic

## External mechanism and project interpretation

Published intraday research on major cryptocurrencies documents a negative
lead–lag relation rather than the equity-style positive diffusion relation. The
largest cryptocurrencies, including BTC, ETH and XRP, negatively predict the
next returns of other coins; the effect also exists among the largest coins.
The proposed mechanism is a flow-of-capital seesaw: investors fly toward a hot
large coin and away from its peers, or flee from a cold large coin and toward
peers.

The paper uses rolling cross-sectional prediction and reports profits after
transaction costs. Candidate 60 does not claim to reproduce its LASSO system.
It extracts the economic decision and adds the missing causal state transition:
a hot/cold leader must remain controlled by the initiating side, while a
separate peer develops opposite price-and-aggressor initiative before entry.

This differs from the failed Candidate 51 `crosslead-v20` experiment. That
experiment fitted unstable BTC/ETH perpetual close relationships to SOL/XRP and
never produced a trade. It did not define a cross-sectional hot/cold leader,
observe executed aggressor flow, or wait for a separate capital-rotation state.

## Reused data and prior-only normalization

Checksum-verified Binance Vision USD-M perpetual one-minute klines are used for
BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT. Quote volume and taker-buy quote volume
produce:

`signed aggressor quote = 2 * taker_buy_quote - total_quote`

`flow imbalance = signed aggressor quote / total_quote`

For every complete 15-minute parent block and complete 5-minute confirmation
block, price return and flow imbalance are normalized by the median absolute
value from the preceding complete crypto day. The current block is excluded by
a one-block shift.

- parent baseline: preceding 96 complete 15-minute blocks, minimum 48;
- confirmation baseline: preceding 288 complete 5-minute blocks, minimum 144;
- ratios above one mean stronger than the strictly prior daily median;
- no z-score, absolute return, volume, volatility, side, symbol or clock
  threshold is fitted.

## Frozen causal scenario

At a complete 15-minute boundary:

1. each possible leader must have nonzero price return and aggressor flow in the
   same direction;
2. both its absolute return and absolute flow must exceed their strictly prior
   daily medians;
3. among eligible leaders, choose the largest product of return ratio and flow
   ratio; ties use BTC, ETH, SOL, XRP.

During the immediately following complete five-minute block:

4. the leader's price and aggressor flow must remain in the parent direction;
5. at least one other asset must show both price and aggressor flow in the
   opposite direction;
6. choose the peer with the largest product of confirmation return ratio and
   flow ratio; ties use the same fixed symbol order.

The proposed trade is in that peer, opposite the leader. This is not immediate
contrarianism against a large move. It requires a later, independently observed
peer initiative consistent with capital rotating away from a hot leader or
toward peers after a cold leader.

Diagnostic entry is the next one-minute open after the five-minute confirmation.
The exact same-direction trade is retained as the placebo.

## Outcomes, cost and one-slot path

- outcome opens: 5, 15, 30 and 60 minutes after entry;
- **15 minutes is the sole primary horizon** fixed before results;
- gross signed return is measured in basis points;
- net return subtracts the project friction floor of 20 bp round trip;
- at identical entry timestamps, select the event with the largest fully
  pre-entry state strength;
- selected events are made non-overlapping for each horizon.

This is an opportunity-set diagnostic, not a fill, risk or NAV claim. A promoted
family must later run through NautilusTrader with the project one-slot and 3%
planned-loss contract.

## Frozen intervals

Development:

- scored entries: **2026-07-06 through 2026-07-12 UTC**;
- two prior days are downloaded for causal baselines;
- one following day is downloaded for outcomes.

Conditional policy-fresh:

- scored entries: **2026-08-03 through 2026-08-09 UTC**;
- it is downloaded only after development eligibility;
- this interval remains untouched because the prior spot/perpetual and
  impact-efficiency families failed development.

## Development eligibility fixed before results

At the primary 15-minute horizon, fresh data are consumed only when:

1. all archives and checksums are valid and minute coverage is complete;
2. all four assets appear at least once as the traded peer;
3. the one-slot path completes at least 14 events in seven scored days;
4. mean and cumulative net-after-20-bp returns are positive;
5. at least three of four peer assets have positive mean net returns;
6. at least four scored days have positive day-level mean net returns;
7. the proposed opposite-leader direction beats the exact same-leader placebo;
8. removing the best event leaves positive cumulative net return.

No adjacent horizon can rescue failure at 15 minutes. No threshold, baseline,
leader rule, peer rule, direction, symbol, date or clock subset is changed after
outcomes. A fresh replication authorizes only a NautilusTrader scenario-family
implementation, not final-system integration.
