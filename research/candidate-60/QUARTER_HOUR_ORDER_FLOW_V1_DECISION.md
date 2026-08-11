# Candidate 60 — quarter-hour synchronized order-flow decision

## Development result

The external mechanism was plausible but the raw project translation was not a
robust trade component.

Using the first ten seconds of Binance aggregate trades, the quarter-hour phase
had positive day-balanced signed returns at 60, 240, 480 and 720 minutes and
outperformed the seven-minute-shift placebo at 240 minutes. However:

- only two of four project assets had positive 240-minute mean signed returns;
- the one-symbol selector proxy was negative at the primary 240-minute horizon;
- the predeclared independent four-hour selector could not rescue the economic
  conclusion;
- the magnitude was far below the project's realistic round-trip friction.

The conditional fresh interval was not consumed.

## Implementation correction

The first independent-sample report contained `NaN` because a pandas timestamp
unit change interpreted exchange microseconds as nanoseconds. The data and raw
clock-phase result were valid; only the independent selector clock was affected.
A policy-identical rerun corrected the unit. This did not alter the already
failed asset-consistency and selector conditions and therefore did not authorize
fresh data.

## Market-model conclusion

A shared quarter-hour clock can coordinate aggressive orders, but the sign of
the first-ten-second flow alone does not identify whether the synchronized flow
is informed, inventory-motivated, immediately absorbed, or already incorporated
into price. Positive long-horizon correlation in a pooled sample is not enough
for a cost-after one-slot day-trading system.

The exact raw-sign policy is closed without searching imbalance cutoffs,
volatility filters, time-zone subsets, asset exceptions or alternative phases on
the consumed July interval. A successor would need a genuine impact-efficiency
state—price displacement relative to flow and liquidity—or source-faithful
queue OFI, not another threshold on the same opening-flow ratio.
