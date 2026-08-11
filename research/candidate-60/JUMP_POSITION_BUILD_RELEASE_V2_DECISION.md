# Candidate 60 — position-build release V2 forensic decision

## Untouched account result

The V2 policy consumed the previously reserved 2026-06-08 through 2026-06-21
interval only after its market model and rules were frozen.

| cell | trades | W/L | PF | return | MDD |
|---|---:|---:|---:|---:|---:|
| two-bar price-reclaim control | 5 | 2/3 | 0.1627 | -6.7218% | 12.5442% |
| OI build + reversal-side taker flow | 1 | 0/1 | 0.0000 | -2.8835% | 3.1369% |

The candidate reduced exposure and drawdown, but it did not identify a positive
conditional distribution. Its only completed trade lost approximately one R.
It is not eligible for integration or further fresh evaluation.

## Event anatomy

The five control events and their pre-entry state were:

1. XRP short, small transient-break-even gain: OI increased about 1.56%, but
   taker flow remained on the impulse side through both confirmation checks;
2. ETH short, near-full loss: OI decreased about 1.67%, while taker flow crossed
   from the impulse side to the reversal side;
3. XRP long, near-full loss: OI increased about 1.21%, and taker flow was already
   marginally on the reversal side at confirmation; this was the only candidate
   entry;
4. BTC long, near-full loss: OI decreased about 1.98%, with confirmation taker
   flow on the reversal side;
5. SOL long, +1.26% account gain at the source horizon: OI decreased about
   1.48%, and taker flow crossed strongly to the reversal side.

Thus every simple quadrant contained both economically plausible stories and
unfavorable outcomes. OI sign did not distinguish contract build from the
identity or vulnerability of the new holders; taker-flow sign did not establish
that the flow was informed, exhausted, hedging, or capable of moving price; and
price reclaim alone did not prove durable reacceptance.

## Market-model conclusion

The exact V1 and V2 OI/taker policies are closed without threshold retuning.
Their useful conclusion is negative but specific:

> Aggregate target-contract OI and taker-ratio signs are insufficient state
> variables for determining whether a completed price impulse has created
> trapped inventory whose exit will fuel a reversal.

A stronger state model must distinguish at least:

- spot-led information from derivative-led leverage pressure;
- forced liquidation from voluntary position transfer;
- aggressor volume from price impact per unit of aggressor flow;
- local reclaim from cross-venue or cross-asset reacceptance;
- fresh inventory whose holders are trapped from fresh inventory that is
  correctly positioned and still funded.

No OI lookback, neutral boundary, confirmation delay, source jump threshold,
stop, target or management value is changed on the consumed April and June
intervals. Any successor must use a genuinely new observable mechanism and an
untouched interval.
