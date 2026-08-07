# Research basis and deductions

## Primary evidence used

- **Cont, Kukanov & Stoikov, “The Price Impact of Order Book Events”**: short-horizon price changes are strongly related to signed order-flow imbalance, with impact conditioned by depth.  Candidate 12 therefore treats taker-buy imbalance as a coarse bar-level confirmation variable, not as a standalone direction signal.
- **Osler, “Currency Orders and Exchange Rate Dynamics”**: stop-loss and take-profit orders cluster near salient technical levels; crossing those clusters can create rapid movement, while support/resistance can arise from order placement.  Candidate 12 records live external levels before they are touched and distinguishes failed crossing from accepted crossing.
- **Intraday time-series momentum in Bitcoin**: predictability varies with activity/volatility and is not well represented by an always-active rule.  Candidate 12 uses relative activity and separate rejection/acceptance scenarios rather than forcing one action in every regime.
- **ICT 2022 Mentorship transcripts/summaries**: old/equal highs and lows are framed as external liquidity; the recurring sequence is liquidity interaction, market-structure shift/displacement, retracement into imbalance, and movement toward external liquidity.  The implementation keeps this sequence but replaces discretionary chart interpretation with observable state transitions.
- **NautilusTrader official backtesting documentation**: completed custom bars must be timestamped at close; historical data is processed for execution before `on_bar`; adaptive high/low ordering reduces fixed OHLC-path bias; L1/bar fill models cannot recreate actual order-book depth.  The runner follows close-time visibility and records the bar-data limitation explicitly.

## Independent synthesis

The useful intersection is not “buy every FVG” or “fade every sweep.”  It is an auction decision:

- **Rejection/absorption**: liquidity is accessed, aggressive flow fails to make durable progress, price reclaims the boundary, then internal structure shifts away from the trapped side.
- **Acceptance/continuation**: liquidity is accessed, closes and aggressive flow sustain price beyond the boundary, then the boundary holds on retest and price reaccelerates toward the next pool.

Both paths share the same live-pool ledger and costed target selection, but their confirmation and invalidation logic are deliberately different.

## Scope limits of the first experiment

The Binance kline `taker_buy_volume` field is trade-flow imbalance, not full-depth OFI.  It can help distinguish absorption/acceptance but cannot reveal queue position, cancellations, hidden liquidity, or market impact.  A bar-based candidate must first show substantial alpha despite conservative fees/slippage; only then is higher-resolution depth validation worth the cost.
