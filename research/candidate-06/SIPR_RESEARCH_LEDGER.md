# Candidate-06 SIPR research ledger

## Inherited terminal decisions

ACSR is rejected after a controlled implementation repair and unchanged replay.
Its 30-minute full, structure-flow ablation and no-impact ablation all produced
the same five fills, zero wins, -2.1527% geometric NAV/day and 14.13% maximum
drawdown.  The 60-minute reference produced no trades.  Absorption plus one
local opposite 5-minute CHoCH is therefore not a durable reversal definition.

SIAR flow surprise also remains rejected as a direction signal.  Impact
efficiency is retained only because it reduced the sealed-week-2 continuation
loss cluster from double-digit trades to two near-breakeven trades.  AFHR's
completed-close freshness and HFF's sweep/response flow stages remain useful
context controls, not standalone alpha.

## External research translated into a testable claim

The next hypothesis does not equate persistent signed flow with predictable
returns.

- Taranto, Bormetti and Lillo show that liquidity adapts to predictable order
  flow, reducing the probability that the expected side moves price.
- Toth et al. connect long-memory order signs to fragmented metaorders and a
  dynamically thin liquidity funnel; persistence can therefore be real while
  local impact remains nonlinear.
- Degryse et al. find persistence in aggressive orders, partial reversal of the
  initial impact, and a slower tendency toward the aggressive-order price.

The machine-level implication is narrower: direction exists only when separate
completed auctions repeatedly convert participation into farther accepted
prices.  Raw flow persistence, one exceptional bar, and one local CHoCH are all
insufficient.

## New structural claim

SIPR uses a 15-minute auction as an intraday state observation and 5-minute
confirmed liquidity for entries.

1. A first completed 15-minute auction must close beyond the inherited prior
   completed range with directional body, range, participation and close
   location.  It creates no trade and suspends any older context.
2. When impact is enabled, direction-aligned residual flow must produce at least
   prior-median displacement efficiency on that first auction.
3. The immediately following completed 15-minute auction must independently
   qualify in the same direction.  Because the first auction is already in the
   prior range, this requires a new accepted extreme rather than repeated
   observation of the same displacement.
4. When impact is enabled, the second auction must independently pass the same
   prior-only efficiency test.
5. Any nonpersistent next auction resets the sequence.  The state cannot wait
   across a convenient gap.
6. Only the confirmed sequence creates a direction context.  The inherited
   confirmed 5-minute swing/equal-pool sweep, separate one-minute response,
   structural bracket, delayed entry, costs and 3% current-NAV risk are unchanged.

## Controlled factor matrix

- `sipr_full`: sequence plus impact efficiency;
- `sipr_sequence_only_ablation`: remove only impact efficiency;
- `sipr_impact_only_ablation`: remove only the second-auction requirement;
- `sipr_raw_15m_reference`: both factors off, ineligible reference.

The fixed priority is declared before execution.  The first eligible variant
passing the existing complete first-week gate is locked unchanged for both
sealed BTC weeks.  No session fitting, direction switch, threshold search,
score-based risk, stop/target change or execution change is permitted.
