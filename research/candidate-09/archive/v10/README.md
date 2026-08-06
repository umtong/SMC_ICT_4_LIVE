# Candidate 09 v10 — positive structural path, incomplete gate

Reproducible implementation-clean NautilusTrader run: GitHub Actions `31116087519`.
Result commit: `fee792bb8b5f1a2864b3ba657e2197c510746b20`.

## Controlled hypothesis

V10 performed the missing exact decomposition of the only previously positive
candidate, v4. It loaded the archived v4 state engine directly and preserved its
completed-auction detector, outside acceptance, accepted-breakout failure,
reversal invalidation and equilibrium target. Baseline removed only the two
components whose v4 trade accounting was negative: continuation entries and
240-minute source levels. Baseline horizons were 15m, 60m and 1440m.

## Frozen-week result

- pooled cost-after daily geometric growth: **+0.680629%**
- pooled NAV multiple: **1.153093x**
- trades: **7**, wins: **5**, losses: **2**
- maximum sampled-segment drawdown: **5.9109%**
- week-a: **+8.0446%**, 6 trades, 66.67% win rate, PF 2.245
- week-b: **0.0000%**, 0 trades
- week-c: **+6.7237%**, 1 winning trade

The gate still failed because pooled growth was below 1%, week-b had no trade,
minimum weekly opportunity was not met, and week-c profit was concentrated in one
trade.

## Exact component ablations

- `with-continuation`: **+0.388962%/day**, 1.084939x, 9 trades
- `with-240m`: **+0.534687%/day**, 1.118496x, 8 trades
- `no-flow`: **+0.124205%/day**, 1.026410x, 9 trades

Every restoration/removal weakened baseline. Therefore the independent strongest
mechanism was precisely:

```text
15m/60m/daily completed auction extreme
-> directional approach
-> outside acceptance with displacement, volume and flow
-> accepted boundary lost with opposite displacement/flow
-> trapped-breakout reversal
-> source-range equilibrium target
```

## Remaining bottleneck

Week-b contained 437 breaches, 169 acceptances and 90 logically confirmed failure
resolutions, but none passed the unchanged cost/target/RR geometry. A favorable
lower-bound reconstruction found two BUY failures whose minimum possible stop
would have allowed more than 1.2 net R; the actual accepted excursion made the
immediate entry untradeable. Thus the next structural path is not to add weaker
patterns, restore continuation, or loosen the RR gate. It is to keep the same
failure logic, target and accepted-extreme invalidation while waiting for a causal
failed-boundary retest only when the immediate failure close is untradeable.

## Classification

**LOGICALLY POSITIVE BUT INCOMPLETE CANDIDATE.**

V10 is retained as the exact control. V11 preserves every immediately tradeable
v10 reversal and stages only otherwise rejected failure resolutions for the first
inside rejection of the failed boundary. `no-retest-salvage` reproduces v10,
`retest-all` tests whether all signals benefit from waiting, and `no-flow` tests
the flow contribution under the new entry path.
