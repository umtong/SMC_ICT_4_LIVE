# Candidate 15 V2 failure evidence

V2 fixed stale state reuse and proved that the lifecycle change was causal rather
than a cosmetic filter:

- D1 replay: two V1 losses became zero trades.
- H1 replay: the one-bar-fresh V1 winner remained and returned `+4.5248%` after costs.
- S1 replay: the stale V1 loss became zero trades.

The separately predeclared U1-U5 screen was still insufficient:

- weekly-reset NAV multiple: `0.9685399563`
- daily geometric growth: `-0.0009128842431938753`
- closed trades: `1`
- wins / losses: `0 / 1`
- maximum interval drawdown: `0.0314600437248`
- liquidation: none
- engine errors: none
- classification: `CANDIDATE15_V2_INSUFFICIENT_ACTIVITY`

## U2 failure decomposition

The sole unseen trade was XRPUSDT FAR long on `2025-12-05 04:04 UTC`.

- router state: fresh `FAILURE`, resolved on the entry bar;
- entry: `2.0974`;
- target: `2.1118`;
- original sweep stop: `2.0862008`;
- proposed stop: `2.0932008`;
- pool: `2.0877`;
- sweep extreme: `2.0863`;
- original market costed R: `0.8165333531`;
- fallback nominal costed R: `1.5728101553`;
- actual stop fill: `2.0930123211`.

The fallback `FULL_DISPLACEMENT_VOID_TRAVERSAL` stop was above the pool and sweep
low. It ended the position while the failed-auction thesis had not reached its
scenario invalidation. The fallback therefore manufactured acceptable R by
changing the meaning of loss, rather than finding more economic space.

V3 enforces the causal invariant for every FAR plan, independent of its named
stop model:

```text
FAR long  -> stop <= original sweep stop
FAR short -> stop >= original sweep stop
missing proof -> no trade
```

U1-U5 are contaminated after V2. V3 classification uses only newly predeclared
V1-V5 intervals.
