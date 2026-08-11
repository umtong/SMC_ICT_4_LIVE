# Candidate 60 — frozen impact-efficiency and delayed-initiative diagnostic

## Market mechanism

The prior experiments showed that the sign of executed flow, open-interest
change, a slow trend label, or a shared clock boundary is not enough to identify
who is paying for the next price move. The missing distinction is **price impact
per unit of aggressive effort**.

A large signed market-order flow can describe two different auctions:

1. **efficient initiative / price discovery** — aggressive flow consumes
   available liquidity and moves price efficiently; if a separately completed
   observation shows the same side still controlling both trades and price, the
   remaining metaorder or information response may continue;
2. **absorbed initiative / inventory transfer** — unusually strong aggressive
   flow produces little price progress relative to the current market regime;
   if a later completed observation shows both opposite aggressive flow and
   opposite price progress, the defended side has taken initiative and the
   initial inventory can unwind.

The entry observation must be distinct from the state observation. Candidate
16 v1 previously collapsed because it used attack effort and reclaim to label a
failed auction and immediately entered on the same bar. This diagnostic instead
uses one completed five-minute parent block to measure effort and impact, one
later completed five-minute block to confirm persistence or release, and only
then observes the next one-minute open.

External work motivates the structure but does not prove it. Multi-level OFI
research finds that price impact and its decay depend on order-flow imbalance
and liquidity; limit-order-book resiliency research distinguishes continuation
from recovery after effective market orders; recent CME Ether work reports that
OFI impact rises after the raw observation and can support short-horizon trading.
This experiment does **not** claim to reproduce queue OFI because the public
Binance one-minute archive contains executed taker flow, not order-level queue
updates.

## Reused public data and exact observations

Checksum-verified Binance Vision USD-M perpetual one-minute klines are used for
BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT. Each row exposes total quote volume and
taker-buy quote volume.

For a fully completed five-minute block:

- `signed_aggressor_quote = 2 * taker_buy_quote - total_quote`;
- `flow_imbalance = signed_aggressor_quote / total_quote`;
- `return_bps = 10,000 * (close / open - 1)`;
- `impact_efficiency = abs(return_bps) / max(abs(flow_imbalance), 1e-6)`.

The current regime baselines are the medians of the preceding 288 completed
five-minute blocks, shifted by one block so the parent observation is excluded.
At least 144 prior blocks are required. The 288-block horizon is one complete
crypto day and therefore spans the full diurnal liquidity cycle; it is fixed
before results and is not searched.

Definitions:

- `flow_ratio = parent abs(flow) / prior-day median abs(flow)`;
- `impact_ratio = parent impact_efficiency / prior-day median impact_efficiency`.

The neutral boundaries are ratios of one. They compare the current state to its
own strictly prior regime and are not fitted on outcomes.

## Frozen causal families

A parent block is eligible only when:

- parent price return and parent aggressive flow have the same nonzero sign;
- parent absolute flow is above its strictly prior one-day median
  (`flow_ratio > 1`).

### `impact_persistence`

- parent `impact_ratio > 1`;
- the immediately following completed five-minute block has both price return
  and aggressive flow in the parent direction;
- proposed direction is the parent direction.

This is the efficient-initiative continuation state.

### `absorption_release`

- parent `impact_ratio < 1`;
- the immediately following completed five-minute block has both price return
  and aggressive flow opposite the parent direction;
- proposed direction is the opposite direction.

This is not an immediate low-impact fade. It requires a separate, later
opposite initiative before entry.

No absolute return, flow, volume, volatility, time-of-day, side or symbol
threshold is used.

## Causal entry, outcomes and one-slot proxy

- parent block: five completed one-minute bars;
- confirmation block: the next five completed one-minute bars;
- diagnostic entry: the next one-minute open after confirmation;
- outcomes: opens 5, 15, 30 and 60 minutes later;
- **15 minutes is the sole primary horizon**, chosen before results because the
  external source-faithful OFI system uses a ten-minute clock cap and because
  this project requires short-lived day-trading opportunity turnover;
- gross signed return is measured in basis points;
- net return subtracts the project friction floor of 20 bp round trip;
- the opposite direction is retained as a placebo.

At each timestamp, the one-slot proxy selects the symbol with the largest
pre-entry state strength. Ties use BTC, ETH, SOL, XRP. Selected events are then
made non-overlapping for each horizon. This is an opportunity-set diagnostic,
not a fill or NAV claim; any promoted family must later run through
NautilusTrader.

## Frozen intervals

Development:

- scored entry dates: **2026-07-13 through 2026-07-19 UTC**;
- two prior days are downloaded for causal regime baselines;
- one following day is downloaded for outcomes.

Conditional policy-fresh:

- scored entry dates: **2026-08-03 through 2026-08-09 UTC**;
- it is downloaded only for a family that earns development eligibility;
- this interval remains untouched because the earlier spot/perpetual diagnostic
  failed development and did not consume it.

## Development eligibility fixed before results

At the primary 15-minute horizon, a family may consume the fresh interval only
when:

1. all daily archives are checksum verified and minute coverage is complete;
2. all four assets produce at least one structurally valid event;
3. the one-slot non-overlap path completes at least 14 events over seven scored
   days;
4. mean and cumulative net-after-20-bp returns are positive;
5. at least three of four assets have positive mean net returns;
6. at least four event days have positive day-level mean net returns;
7. the proposed direction outperforms the exact opposite-direction placebo;
8. removing the single best net event leaves positive cumulative net return.

No adjacent horizon can rescue failure at 15 minutes. No threshold, baseline,
state ordering, side, symbol, clock subset or interval is changed after results.
A fresh replication authorizes a NautilusTrader scenario-family implementation
only; it is not final-system evidence.
