# Candidate 15 — Sequential Price–Flow Response Router

Candidate 15 keeps Candidate 14's frozen NautilusTrader detector, order,
portfolio, cost and current-NAV risk path. It inserts two causal validity checks
before portfolio arbitration:

1. a sweep-response state must resolve recently enough to belong to the same new
   auction leg as entry confirmation;
2. a FAR stop must remain at or beyond the sweep invalidation that defines the
   failed-auction thesis.

```text
external liquidity trade-through
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

fresh FAR plan
      ↓
stop beyond original sweep invalidation?
   ↙                                  ↘
yes                                  no
trade                       C15_STOP_INSIDE_SWEEP_INVALIDATION
```

## Research sequence

### V1 — state aliasing addressed, lifecycle broken

V1 added a sequential local price/flow response classifier. Its three-week
screen produced five trades, one win and four losses. The only core winner used
a response one bar after resolution; the core losses reused a frozen resolution
approximately 8, 13 and 27 bars later. A separate session trade bypassed the
router.

### V2 — causal decision lease

V2 made each resolution usable only on its bar and the immediately following
completed bar. Known stale losses disappeared and the known fresh winner
remained. On five new weeks, however, only one trade occurred and lost.

That trade revealed a different fault: its original sweep stop yielded only
`0.8165R`, so Candidate 14's fallback moved the stop inside the reclaimed sweep
to report `1.5728R`. The position stopped while price was still above the swept
low and pool. This changed the definition of invalidation to manufacture economic
space.

`V1_FAILURE.md` and `V2_FAILURE.md` preserve both rejected iterations.

### V3 — scenario-terminal invalidation

V3 checks every FAR plan at the shared portfolio boundary:

- long stop must be at or below the original sweep stop;
- short stop must be at or above the original sweep stop;
- absent or non-finite proof fails closed.

The rule does not target the losing stop-model name. It enforces the scenario's
causal loss boundary for market and passive plans alike. Rejected plans receive
an explicit lifecycle transition before leadership or arbitration.

`SESSION_I7` remains observed and logged but fails closed as
`C15_UNROUTED_SCENARIO_FAMILY`; it lacks Candidate 15's continuously observed
external-liquidity response episode.

## Response evidence

For each newest sweep extreme, the router calibrates a non-negative
contemporaneous relation between one-minute log return and signed taker-flow
pressure over completed pre-event bars. Each later completed bar contributes:

1. directional price response;
2. price conversion under aggressor-pressure magnitude;
3. directional residual beyond calibrated impact;
4. close occupancy beyond the latest crossed external boundary.

A symmetric `log(9)` boundary with `log(2)` full-agreement increments is a fixed
methodological convention, not a calibrated posterior probability. The available
bar field is an aggressor-flow proxy, not full limit-order-book OFI.

## Preserved invariants

- NautilusTrader owns orders, fills, fees, margin, positions and NAV.
- Current whole-account NAV and 3% planned loss determine quantity.
- At most one pending entry or open position exists across all four instruments.
- Candidate 14's direction, target, leadership and cost model are unchanged.
- No outcome-fitted route whitelist, risk multiplier or leverage cap is added.
- `UNRESOLVED` and `STALE` are real no-trade states.

## V3 validation protocol

M1/M2 replay V2's known winner and invalidation failure. C1-C5 replay Candidate
13's published 7/7 weeks to measure how much valid opportunity survives the new
router; they are diagnostic only. V1-V5 were committed before V3 outcomes and
alone determine classification.

```bash
for interval in M1 M2 C1 C2 C3 C4 C5 V1 V2 V3 V4 V5; do
  bash research/candidate-15/run_week.sh "$interval"
done
python research/candidate-15/aggregate.py
```

Isolated weeks are screening evidence, not a continuous-account success claim.

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
