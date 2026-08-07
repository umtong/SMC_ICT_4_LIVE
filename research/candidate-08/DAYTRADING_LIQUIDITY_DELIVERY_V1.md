# Day Liquidity Delivery Router V1

## Correction of research direction

This candidate replaces the rejected microstructure-repricing direction. It is a day-trading
scenario, not a scalp. BTC, ETH, SOL and XRP are only liquid testbeds. The same causal market logic
must transfer without symbol-specific thresholds.

The previous session-sweep probe was not evidence that session liquidity is useless. It was evidence
that a sweep is not a directional thesis: seven proxy trades produced five stops because every sweep
was forced into a reversal without first establishing the higher-timeframe draw on liquidity. The
repair is therefore not a slower confirmation or a looser threshold. It is a hierarchy of decisions.

## Decision hierarchy

1. **Direction — completed H4 draw on liquidity.** A completed H4 displacement close through a
   causally confirmed H4 swing establishes direction. The latest confirmed opposite swing becomes
   the dealing-range origin and invalidation reference. A session clock never chooses direction.
2. **Destination — active external liquidity.** At the session interaction, freeze the nearest active
   completed day/week pool in the draw direction. A completed H4 pool is a fallback only when it is
   still materially external. There is no fitted-R target.
3. **Location — premium/discount.** A long must enter in discount and a short in premium of the frozen
   origin-to-target dealing range. This prevents a correct narrative from becoming a late chase.
4. **Route — completed session auction.** The completed source session can deliver toward the draw by
   either an opposite-side raid and reclaim or a same-side acceptance and retest. These are distinct
   scenarios, not interchangeable candle labels.
5. **Timing — separate M5 delivery.** Only after the route is observable do we freeze the latest
   causally confirmed opposing M5 swing. A later displacement must close through it and form a
   three-bar FVG. Entry occurs on the first subsequent M5 mitigation that closes back with the draw.
6. **Execution — existing NautilusTrader path.** The first observable 10-second replay bucket after
   the completed M5 bar timestamps the market OUO bracket and supplies the causal stop-slippage
   reserve. Ten-second order flow is not alpha.

## Frozen routes

### DRAW_ALIGNED_RAID_REVERSAL

For a bullish H4 draw, the completed source-session low is swept and the M15 bar closes back inside.
The raid low is the structural reference. A separate bullish M5 MSS/displacement/FVG and its first
qualified mitigation are required before a long. The bearish route is symmetric.

### DRAW_ALIGNED_ACCEPTANCE_CONTINUATION

For a bullish H4 draw, the completed source-session high is accepted by an M15 displacement close.
A later, separate M15 retest must touch and hold the accepted side. Its low is the structural
reference. A separate bullish M5 MSS/displacement/FVG and first qualified mitigation are then
required. The bearish route is symmetric.

## Time contract

- Asia source: 00:00–08:00 UTC; Europe route: 08:00–13:00 UTC.
- Europe source: 08:00–13:00 UTC; US route: 13:00–18:00 UTC.
- At most one route is armed per route window. A failed acceptance or failed first FVG mitigation is
  not retried.
- Maximum holding time is six hours. The US route therefore cannot deliberately survive beyond the
  UTC trading day.

The timetable is shared by all four testbeds. It identifies when inventory is complete and when
participation changes; it is not a fitted asset session.

## Risk and target contract

- Current shared-account NAV remains the sizing basis.
- Planned loss remains exactly 3% of current NAV.
- Expected loss includes structural entry-to-stop distance, both fees, one adverse entry tick,
  causal stop slippage and causal funding reserve.
- Raid stop: beyond the observed M15 raid extreme plus the fixed M5-ATR execution buffer.
- Acceptance stop: beyond the observed M15 retest extreme plus the same buffer.
- Target: frozen active completed HTF external liquidity. No arbitrary take-profit multiple and no
  symbol-specific nominal cap are introduced.
- Across BTC, ETH, SOL and XRP, pending entries plus positions remain globally limited to one.

## Efficient validation

The first frozen BTC week asks one question: does the complete H4 → session route → M5 delivery
sequence occur several times and retain enough frozen target distance after actual costs? Diagnostics
are read in causal order:

1. active H4 draw;
2. active HTF target;
3. draw-aligned session interaction;
4. separate M5 MSS/displacement/FVG;
5. first FVG mitigation;
6. premium/discount location;
7. cost-after geometry;
8. executed signal.

A zero-trade result is not repaired by threshold relaxation. The failed transition determines whether
one economically different revision is justified. Only a clean first-week pass opens the two other
frozen BTC weeks; only three unchanged BTC passes open unchanged transfer to ETH, SOL and XRP.
