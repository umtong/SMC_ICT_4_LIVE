# Candidate 20 — Actual-Trade Execution Clock

Candidate 20 changes no Candidate 19 alpha, threshold, risk, cost, stop, target,
or FOK order policy. It fixes a measurement/execution-clock mismatch.

The common runner had downloaded Binance aggTrades for features but replayed
only one-minute bars through Nautilus. With 250 ms configured insertion latency
and no event between bars, a close-generated order could settle after the next
minute completed. A BUY limit then treated a close back inside the failed range
as a better fill even though the market-state hypothesis was invalid.

Candidate 20 writes one actual aggregate trade per minute to the existing
Nautilus catalog: the first trade at least one second after the boundary, with a
first-trade fallback. This sparse clock is not a matching engine. Nautilus still
owns event ordering, latency, FOK matching, contingent orders, fees, positions,
margin, liquidation, portfolio and NAV. Bars remain the strategy clock and the
conservative stop/target execution source.
