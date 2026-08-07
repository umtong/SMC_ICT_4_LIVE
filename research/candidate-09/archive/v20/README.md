# Candidate-09 v20 — discarded completed-auction value rejection

## Decision

**Discarded as a primary alpha family after an implementation-clean frozen-week run.**

The baseline formed value from a completed 60-minute auction, observed a later value-edge
probe, required a return into value and then a value-edge retest rejection before targeting
the frozen equilibrium. It produced zero trades in all three fixed weeks. The exact
single-variable controls did not reveal a credible structural repair path:

- `no-retest`: three trades, pooled cost-after daily geometric growth **-0.1207%**;
- `no-flow`: one winning trade, **+0.1694%/day**, but one active week and 100% of positive
  PnL from that single trade;
- `range-midpoint`: zero trades.

A one-trade positive control is not evidence that aggressor-flow confirmation should be
removed. It is retained only as a diagnostic observation.

## Failure mechanism

Across the fixed baseline screen:

- 501 completed 60-minute value auctions were frozen;
- 460 value-edge probes were observed;
- 327 returned into value with the required opposite response;
- 119 reached equilibrium before an edge-retest entry;
- 132 were reaccepted outside;
- 40 never retested and rejected in time;
- 36 retest resolutions were untradeable after full costs and the unchanged 1.2 net
  reward-to-risk contract.

The return-to-value event occurred frequently enough, but there was no economic interval
between confirmation and the already-observed equilibrium. Removing the later retest did
not create positive expectancy. The problem is therefore not merely an over-strict entry
confirmation.

## Valid parts preserved

1. A completed auction is frozen before any later probe; no current-auction future data is
   used.
2. Equilibrium-before-entry expiration correctly prevents chasing a completed move.
3. Outside reacceptance cleanly invalidates the mean-reversion claim.
4. The event state machine explains whether opportunity was lost to timing, reacceptance,
   geometry, cost or insufficient reward.
5. The one-minute typical-price volume distribution remains explicitly labelled a coarse
   auction proxy, not historical L2 reconstruction.

## Carried-forward conclusion

Loss of a value edge and return into value do not provide a durable mean-reversion entry
under the available one-minute aggregate observations. The next candidate changes the
economic question to continuation after opposing aggressor flow fails to push price back
inside a completed source auction.

## Reproducibility

- Workflow run: `31159820387`
- Trigger commit: `257f56cab7e5dcada730db1c9261e9a3ec459d0a`
- Result commit: `fcaa71ccfc99037b7c70fcfdc19e5eb774307873`
- Doctor, compile, contracts, NautilusTrader screen and account reconciliation completed.
- Full artifact: `candidate-09-v20-31159820387`
