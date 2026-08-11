# Candidate 60 — frozen spot/perpetual price-discovery diagnostic

## Economic question

A perpetual-futures move can be information discovery, leveraged inventory
pressure, or merely the arbitrage response to spot. The same price candle is not
tradable for the same reason in all three states.

This diagnostic asks two distinct questions using the same asset on Binance
spot and USD-M perpetual markets:

1. **Spot-led information transfer:** did spot price and spot aggressive flow
   move together more strongly than the perpetual, leaving the perpetual as the
   plausible follower?
2. **Unconfirmed derivative pressure:** did perpetual price and perpetual
   aggressive flow move together more strongly while both spot price and spot
   aggressive flow refused to confirm, leaving the derivative move as a
   plausible leverage/inventory excursion?

The first state predicts continuation in the spot direction. The second predicts
perpetual reversion against its own move. These are different market mechanisms
and are diagnosed separately.

Candidate 51's earlier `crosslead-v20` is not this experiment. It fitted BTC/ETH
perpetual close-to-close relationships to SOL/XRP and produced zero candidates
because the relationship sign was unstable. It did not observe spot, same-asset
basis, or spot-versus-perpetual aggressive flow.

## Reused public data

The implementation adapts Candidate 05's checksum-verified Binance Vision
minute-kline ingestion. Both spot and USD-M perpetual one-minute klines contain
quote volume and taker-buy quote volume. At a completed interval:

`signed aggressive quote = 2 * taker_buy_quote - total_quote`

`flow imbalance = signed aggressive quote / total_quote`

Positive values mean buyer-taker dominance and negative values mean
seller-taker dominance. This is an executed-flow observation, not resting-book
OFI and is never described as queue imbalance.

## Frozen completed-five-minute states

All observations use five fully completed one-minute bars. Let `r_s`, `f_s` be
spot return and spot flow imbalance, and `r_p`, `f_p` the corresponding
perpetual values.

### `spot_lead_follow`

The proposed direction is `sign(r_s)` only when:

- `r_s` is nonzero and `sign(f_s) == sign(r_s)`;
- `abs(r_s) > abs(r_p)`;
- `abs(f_s) > abs(f_p)`.

This means spot price and spot urgency agree and both dominate the derivative's
same-window response. No absolute return, z-score, volume or symbol threshold is
used.

### `perp_lead_fade`

The proposed direction is `-sign(r_p)` only when:

- `r_p` is nonzero and `sign(f_p) == sign(r_p)`;
- `abs(r_p) > abs(r_s)`;
- `abs(f_p) > abs(f_s)`;
- `sign(r_s) != sign(r_p)`;
- `sign(f_s) != sign(r_p)`.

This requires both spot price and spot aggressors to reject the derivative
move, rather than merely lagging by a smaller amount.

## Causal entry and outcomes

- the state is known only after a five-minute block closes;
- diagnostic entry is the next one-minute open in the perpetual contract;
- exits are measured at 5, 15, 30 and 60 minutes;
- **30 minutes is the sole primary horizon** fixed before results;
- gross signed return is measured in basis points;
- net diagnostic return subtracts the project friction floor:
  `2 * (7.5 all-in cost + 2.5 adverse slippage) = 20 bp`;
- funding reserve is reported separately and does not make a losing short-hold
  event look profitable;
- the opposite-direction result is retained as the family placebo.

This is a diagnostic, not a fill or NAV claim.

## One-slot selector proxy

At each completed five-minute boundary, if several assets produce the same
family, select the asset with the largest absolute leader/follower return gap.
Ties use the fixed order BTC, ETH, SOL, XRP.

For each horizon, selected events are then made non-overlapping: no new event is
counted until the preceding diagnostic holding interval ends. This approximates
the opportunity set under the project's single global slot without pretending
to be a portfolio simulation.

## Frozen intervals

Development:

- event dates: **2026-07-27 through 2026-08-02 UTC**;
- one following UTC day is downloaded for forward outcomes.

Conditional policy-fresh:

- event dates: **2026-08-03 through 2026-08-09 UTC**;
- one following UTC day is downloaded for forward outcomes;
- it is consumed only for a family that earns development eligibility.

The quarter-hour experiment did not consume its reserved July 27–August 2
interval. Neither interval is read before this market model, primary horizon and
all decision rules are frozen.

## Development eligibility fixed in advance

A family may consume the predeclared fresh interval only when, at the primary
30-minute horizon:

1. all four spot and perpetual datasets are checksum verified and complete;
2. all four assets produce at least one structurally valid event;
3. the non-overlapping one-slot selector completes at least 14 events over the
   seven event days;
4. its mean and cumulative **net-after-20-bp** return are positive;
5. at least three of four assets have positive mean net return;
6. at least four of seven event days have positive day-level mean net return;
7. the same selector in the opposite direction performs worse;
8. removing the single best net event leaves positive cumulative net return.

No 5-, 15- or 60-minute result can substitute for failure at 30 minutes. No
threshold, side, symbol, clock subset or interval is changed after outcomes.

A fresh replication authorizes a NautilusTrader scenario-family implementation
only. It is not final-system evidence. Failure closes the exact structural state
without tuning the dominance inequalities or primary horizon on these dates.
