# BAVR Research Basis

## Primary sources used

1. Binance public-data repository, `aggTrades` and checksum contract:
   https://github.com/binance/binance-public-data
2. Cont, Kukanov and Stoikov, *The Price Impact of Order Book Events*,
   Journal of Financial Econometrics, DOI 10.1093/jjfinec/nbt003.
3. Cont and de Larrard, *Order book dynamics in liquid markets*,
   arXiv:1202.6412.
4. Taranto, Bormetti and Lillo, *The adaptive nature of liquidity taking in
   limit order books*, arXiv:1403.0842.
5. Jusselin, Mastrolia and Rosenbaum, *Optimal auction duration: A price
   formation viewpoint*, arXiv:1906.01713.
6. Brogaard, Hendershott and Riordan, *Price Discovery without Trading:
   Evidence from Limit Orders*, Journal of Finance, DOI 10.1111/jofi.12769.

## Translation into the candidate

The literature supports separating order-flow persistence from realized price
impact and treating market state as conditional on liquidity and queue/order
flow.  It does not establish that a 70% value area or POC reversion is
profitable.  BAVR therefore treats those constructs as an explicit, falsifiable
auction-state hypothesis and tests it with real transaction volume at price.

The fixed 70% value-area convention is not optimized on candidate returns.  Its
role is to define where most completed exchange volume was accepted.  The
profitability claim is entirely delegated to the frozen NautilusTrader campaign.

## ICT terminology crosswalk

- dealing range / equilibrium -> completed value area and POC;
- liquidity sweep -> aggressive excursion beyond VAH/VAL;
- displacement / market structure shift -> separate response away from the
  failed outside auction;
- draw on liquidity -> prior POC first, opposite value edge second;
- invalidation -> repeated outside closes with aligned aggressive flow.

ICT educational material is used only to preserve scenario ordering and
terminology.  It is not used as empirical performance evidence, and no video
threshold is copied into the test.
