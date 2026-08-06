# Candidate 09 v12 — native passive-limit salvage failed

Reproducible implementation-clean NautilusTrader run: GitHub Actions `31118219871`.
Result commit: `a8a5b289258909ec958043258449ae32cb29d3e4`.

## Controlled hypothesis

V12 preserved every immediately executable v10 market reversal. Only a confirmed
accepted-breakout failure rejected by the unchanged v10 cost/target/RR geometry
could submit a native Nautilus GTC limit bracket at the already observed failed
boundary. The order waited up to 12 bars for a retest. Stop remained beyond the
original accepted excursion, target remained the v4 source-range equilibrium, and
quantity was calculated from expected limit fill, full composite costs, and the
same 3% current-NAV loss budget.

## Frozen-week result

- baseline pooled cost-after daily geometric growth: **+0.534724%**
- pooled NAV multiple: **1.118505x**
- baseline trades: **8**, wins: **5**, losses: **3**
- week-a: **+8.0446%**, 6 market trades
- week-b: **0.0000%**, 0 trades
- week-c: **+3.5225%**, one losing limit trade plus one winning market trade
- maximum sampled-segment drawdown: **5.9109%**

Ablations:

- `no-limit-salvage`: exact v10 control, **+0.680629%/day**, **1.153093x**, 7 trades
- `limit-all`: **-0.289678%/day**, **0.940898x**, 2 trades, both losses
- `no-flow`: **+0.030091%/day**, **1.006338x**, 11 trades

The native LIMIT entry, GTC bracket, pending-order timeout, cancellation, one
pending/open position constraint, fill accounting, and full-cost sizing all
executed without implementation error. The economic hypothesis failed: the only
additional filled baseline limit trade was a full planned loss and displaced part
of the stronger v10 week-c path. Forcing passive entry on every reversal converted
the candidate to two losses.

## Classification

**LOGIC_ERROR_NO_STRUCTURAL_PATH for passive failed-boundary limit salvage.**

`no-limit-salvage` outperformed baseline and exactly reproduced v10. Passive entry
was therefore not retained as a complete-candidate improvement.

## Valid parts retained

- native Nautilus limit-bracket and timeout implementation is reusable
- v10 immediate market reversals remain the strongest verified control
- order flow remains materially useful; removing it nearly eliminated pooled growth
- a failed boundary is a meaningful causal state, but merely resting at it does not
  supply sufficient directional edge

V13 changes the invalidation rather than the entry timing. Every valid v10 signal
is preserved. Only otherwise rejected accepted-breakout failures are re-evaluated
with a stop beyond the failed boundary and failure bar, because renewed acceptance
beyond that boundary invalidates the reversal once the prior accepted auction has
already failed. The equilibrium target and all costs remain unchanged.
