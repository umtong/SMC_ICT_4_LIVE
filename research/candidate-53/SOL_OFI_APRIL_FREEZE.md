# Candidate 53 — SOL true-L1 OFI April confirmation freeze

This confirmation is frozen before opening any April 2024 OFI outcome.

Development evidence already inspected:
- the generic four-asset q90/240 continuation mechanism is NOT accepted as a
  universal system because BTC/ETH/XRP were inconsistent across Jan–Mar;
- SOLUSDT, however, was cost-positive under the unchanged frozen mechanism in
  all three inspected months (January, February, March 2024).

This creates one explicit development hypothesis, not a final asset-specific
system: a high-reflexivity latent state present persistently in SOL may be what
makes the external true-L1 OFI delayed-impact mechanism tradeable.  April is
used only to decide whether this mechanism deserves deeper state research.

Frozen April confirmation rule — unchanged from L1_OFI_FREEZE.md:
- symbol: SOLUSDT only for this confirmation experiment;
- participation clock: each UTC day's threshold is prior 7 complete UTC days'
  median futures quote notional divided by 30;
- true Cont-style best-bid/best-ask OFI, normalized by mean displayed top depth;
- absolute OFI >= causal trailing 90-participation-bar q90;
- direction = sign of OFI (continuation);
- event is known only after the participation bar completes;
- entry proxy = strictly next 1m open;
- fixed diagnostic horizon = 240 minutes;
- current project diagnostic hurdle = 21 bp round trip;
- non-overlap diagnostic rejects events while the prior 240m event is active.

Data contract:
- official Binance Vision checksum-verified 1m futures klines;
- official monthly bookTicker archives, exact chronological reconstruction by
  original observed/transaction timestamp; March is warmup only;
- confirmation outcome interval: 2024-04-08 through 2024-04-14 inclusive.

No April result may change quantile, horizon, direction, participation-clock
construction, cost hurdle, or entry timing.  A failure discards the persistent
SOL mechanism hypothesis.  A pass does NOT justify an asset-name router; it
only justifies searching for a transferable latent liquidity/reflexivity state
and then evaluating that state on untouched data before NautilusTrader promotion.
