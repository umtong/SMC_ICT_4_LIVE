# Candidate 1k — exact-route causal auction system

This branch does not treat the previous policy as a benchmark. It reuses the data,
causal labeling and account-routing engineering that already works, while replacing
one central decision error: previous episode plans chose an arbitrary 1.0–2.0R grid
inside the route. Candidate 1k exits the whole position at the first still-unconsumed
opposing semantic-liquidity or causal 24h volume obstacle. If that real destination
cannot pay at least 1R from a structural invalidation stop, there is no trade.

The common decision grammar is:

1. a pre-existing semantic liquidity boundary supplies directional context;
2. the interaction becomes either failed-auction reversal or accepted-auction continuation;
3. price/volume, common-market, basis and OI behavior describe ownership;
4. the first-return zone refines entry; it does not vote on direction;
5. stop and exact structural target are immutable before entry;
6. one account slot arbitrates BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
7. completed positions exit only at the predeclared TP or SL.

The first diagnostic intentionally uses separated short periods. Its purpose is to
expose implementation and market-logic errors quickly; it is not long-run evidence.
