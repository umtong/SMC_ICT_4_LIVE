# Candidate 15 — Sequential Price–Flow Response Router

Candidate 15 keeps Candidate 14's frozen NautilusTrader detector, order,
portfolio, cost and current-NAV risk path. It adds an online state router between
external-liquidity interaction and inherited FAR/AAC confirmation.

```text
external liquidity trade-through
        ↓
latest-extreme response episode
        ↓
price / aggressor conversion evidence
   ↙          ↓           ↘
FAILURE   UNRESOLVED   ACCEPTANCE
   ↓          ↓           ↓
fresh FAR   NO TRADE    fresh AAC
   \          |           /
      unused after one following bar
                    ↓
                  STALE
                    ↓
                 NO TRADE
```

## V1 and the structural failure

V1 correctly separated local response states but stored the first resolution as
a permanent episode label. Its weekly-reset screen lost 7.72% compounded: five
trades, one win and four losses. The only core winner entered one bar after state
resolution; the three core losses reused a resolution roughly 8, 13 and 27 bars
later. A fifth loss came from `SESSION_I7`, which Candidate 14 injects at the
portfolio layer and therefore bypassed the router.

`V1_FAILURE.md` preserves the evidence and the exact causal decomposition.

## V2 causal decision lease

A response resolution is an event, not an enduring market regime. V2 allows an
inherited structural confirmation to consume the decision on the resolution bar
or the immediately following completed bar. It then changes the router state to
`STALE`, which cannot enter. A new sweep extreme starts a fresh episode.

This timing follows the strategy's causal contract: entry, invalidation and
target must belong to the same new auction leg. It is not a profitable-trade
lookback fitted to return outcomes.

`SESSION_I7` remains observed and logged but fails closed with
`C15_UNROUTED_SCENARIO_FAMILY`. It lacks the continuously observed compatible
external-liquidity episode needed to make the Candidate 15 decision. A future
session router must be researched and validated independently before that family
can contribute opportunities.

## State evidence

For each newest sweep extreme, the router calibrates a non-negative
contemporaneous response between one-minute log return and signed taker-flow
pressure over completed pre-event bars. Each subsequent completed bar adds four
bounded evidence channels:

1. directional price response;
2. price conversion under aggressor-pressure magnitude;
3. directional response unexplained by calibrated impact;
4. close occupancy beyond the latest crossed external boundary.

A symmetric `log(9)` boundary with `log(2)` full-agreement increments is a fixed
methodological convention, not a calibrated posterior probability. The available
bar field is described as an aggressor-flow proxy, not full limit-order-book OFI.

## Preserved invariants

- NautilusTrader owns orders, fills, fees, margin, positions and NAV.
- Current whole-account NAV and 3% planned loss determine quantity.
- At most one pending entry or open position exists across all four instruments.
- Candidate 14's entry, invalidation, target and leadership rules are unchanged.
- No outcome-fitted route whitelist, risk multiplier or leverage cap is added.
- `UNRESOLVED` and `STALE` are real no-trade states.

## Validation protocol

D1/H1/S1 are contaminated V1 mechanism replays. U1-U5 were committed before V2
outcomes and alone determine the V2 screen classification. The five confirmation
weeks remain weekly-reset evidence; they cannot establish long-run success
without a frozen continuous-account run.

```bash
for interval in D1 H1 S1 U1 U2 U3 U4 U5; do
  bash research/candidate-15/run_week.sh "$interval"
done
python research/candidate-15/aggregate.py
```

## Research basis

- Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*,
  arXiv:1011.6402.
- Adams & MacKay, *Bayesian Online Changepoint Detection*,
  arXiv:0710.3742.
- Abhishek & Mannor, *A nonparametric sequential test for online randomized
  experiments*, arXiv:1610.02490.
- Hu & Zhang, *Stochastic Price Dynamics in Response to Order Flow Imbalance*,
  arXiv:2505.17388.

`RESULT.md` and `aggregate.json` are generated from fresh GitHub Actions
Nautilus runs.
