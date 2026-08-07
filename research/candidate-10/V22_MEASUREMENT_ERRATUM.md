# Candidate 10 v22 Measurement Erratum — Live Modeled-Impact NAV

## Classification

The v22 NautilusTrader execution, signal timing, source/target pools, order
lifecycle and raw account accounting completed cleanly. A later audit found a
separate **measurement and risk-sizing implementation defect** in the
size-dependent impact overlay.

The modeled impact cost was subtracted only after the run. Therefore the next
trade's 3% planned-loss budget used raw Nautilus account equity instead of:

```text
current whole-account Nautilus equity
− all modeled impact already incurred on earlier fills
```

This does not add a new risk guard. It corrects the existing project formula by
making all declared costs part of current account NAV before the next quantity
is calculated.

## Reproduced defect in v22

The first trade was sized correctly because no prior modeled impact existed.
For each later trade, the table below recomputes the correct budget from the
unchanged 3% rate.

| Trade | Raw engine NAV at entry | Prior modeled impact | Recorded 3% budget | Correct 3% budget | Overstatement |
|---:|---:|---:|---:|---:|---:|
| 1 | 100,000.0000 | 0.0000 | 3,000.0000 | 3,000.0000 | 0.0000% |
| 2 | 97,320.1478 | 329.1518 | 2,919.6044 | 2,909.7299 | 0.3394% |
| 3 | 94,627.6613 | 562.3707 | 2,838.8298 | 2,821.9587 | 0.5979% |
| 4 | 92,157.6424 | 940.3955 | 2,764.7293 | 2,736.5174 | 1.0309% |
| 5 | 89,701.9751 | 1,255.4085 | 2,691.0593 | 2,653.3970 | 1.4194% |
| 6 | 89,748.8990 | 1,569.1338 | 2,692.4670 | 2,645.3930 | 1.7795% |
| 7 | 87,413.9966 | 1,933.3144 | 2,622.4199 | 2,564.4205 | 2.2617% |
| 8 | 85,163.6101 | 2,311.3698 | 2,554.9083 | 2,485.5672 | 2.7897% |

A second verdict defect was also present: v22's promotion fields counted wins
and profit concentration from raw engine PnL. The only nominal engine winner
made `+46.9238 USDT` before modeled impact but `-266.8015 USDT` after it. It was
therefore not an economically positive trade.

## Evidence interpretation

The prior v22 metrics are retained as historical diagnostics but are not exact
3%-of-current-all-cost-NAV evidence. The dominant directional observation is
still useful as a hypothesis source:

- all seven `CLEARING` acceptance continuations lost after declared costs;
- the one `BUILDING` continuation also lost;
- no trade reached the external session target.

However, exact NAV, quantity and gate comparisons must come from a rerun with
fill-time cost debits.

## Controlled repair

The repair changes no market logic, threshold, data, seed, entry, stop, target,
fee or risk rate.

1. NautilusTrader remains authoritative for fills, commissions, positions and
   raw account NAV.
2. Expected size-dependent impact is debited in a conservative side ledger at
   each actual entry and exit fill timestamp.
3. Every later fixed-point quantity uses raw whole-account NAV minus all prior
   modeled impact debits.
4. Planned loss is checked against exactly 3% of that all-cost NAV.
5. Daily NAV, intraday drawdown, wins, losses and profit concentration used for
   promotion are recomputed after all declared costs.
6. A regression audit rejects any run whose per-trade ledger, risk budget or
   final conservative NAV fails to reconcile.

The v23 full and exact v22-mapping ablation both use this identical repaired
ledger, so their causal comparison changes only OI directional semantics.
