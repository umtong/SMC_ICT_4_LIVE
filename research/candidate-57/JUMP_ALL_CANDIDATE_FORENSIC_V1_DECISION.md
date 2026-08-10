# 4h jump reversal — all-candidate causal episode decision

This is a forensic mechanism decision, not a binary gate.  The 2025-12-01
through 2025-12-14 interval is development data.  The actual account result
remains the NautilusTrader one-slot run; non-trading shadow paths are used only
to reveal candidates hidden by routing and slot occupancy.

## Correct unit of frequency

The audit observed 21 symbol-level candidates, but they came from only nine
completed 4-hour market-event boundaries.  Five boundaries produced multiple
same-direction candidates across the four majors.  Those simultaneous asset
signals are not 21 independent opportunities and must not be counted as such.

The economically relevant opportunity density was therefore approximately
nine broad/asset-specific causal boundaries over 14 calendar days, not 21
trades over 14 days.  The actual account completed eight trades because one new
boundary occurred while the previous position was being closed at its source
horizon.

The family remains a valuable high-payoff specialist, but it cannot satisfy the
final independent-trade-frequency requirement by itself.

## Actual account

The frozen 0.4R arm / 1.0R escape transient-management policy completed eight
trades:

- six wins and two losses;
- NAV 100,000 -> 111,192.91 USDT;
- +11.193% over 14 days;
- +0.7607% geometric daily growth;
- PF 3.731;
- 8.71% maximum drawdown.

This result is already strong enough to justify mechanism work, but it is still
below the project-wide daily-growth and independent-frequency objectives.

## What the account did not show

The strategy's ordinary diagnostics reported 18 actionable symbol candidates
because it returned early whenever the global slot was occupied.  Evaluating
all four symbols before that early return found 21 candidates:

- 13 were not executed;
- nine of those 13 had positive diagnostic shadow outcomes;
- the non-executed group had mean +0.402R and shadow PF 2.74;
- three candidates were hidden specifically by an open account slot, and all
  three were positive, around +0.61R to +0.76R.

These are not 13 additional independent trades.  Twelve were alternate symbols
at collision boundaries, and the three slot-hidden candidates all belonged to
one new 4-hour boundary.  The information value is that the current account
router is leaving payoff on the table, not that the raw trade count can be
multiplied.

## Arbitration failure

There were five collision boundaries.  The source router chose the highest
absolute causal z-score.  Under the diagnostic shadow path, its selected symbol
was the highest-R candidate in zero of the five groups.

The largest economic miss occurred on 2025-12-07 19:59 UTC:

- selected BTC short: about +1.25R;
- rejected ETH short: about +2.93R;
- rejected SOL short: about +3.06R.

At 2025-12-11 03:59 UTC, the selected ETH long exited near break-even while the
rejected XRP long produced about +0.24R.  Other collisions were either all
structural losses or all near-break-even, so changing the symbol would not have
fixed the market-event state.

In this development sample, choosing the *least* extreme qualifying z-score at
each boundary produced approximately +5.55R across the nine boundaries versus
+3.49R for the current highest-z rule.  This is a post-outcome diagnostic, not a
tradable result.  It motivates exactly one fresh comparison; it does not prove
that minimum z-score is generally optimal.

The causal interpretation is plausible: after the 2-sigma threshold already
establishes an abnormal move, the most standardized-extreme asset can retain
more continuation risk, while a less climactic peer may offer more reversible
auction space.  A fresh comparison is required before reusing that policy.

## Market-event state failure

All four candidates at 2025-12-09 15:59 UTC hit the structural stop and produced
virtually no favorable excursion.  No cross-sectional arbitration could repair
that boundary.  The entire market-event state was continuation rather than
reversal.

The failed event's impulse candles closed extremely near their directional
extremes with very small terminal rejection wicks.  The successful broad
reversal on 2025-12-07 also closed high in the range, so candle geometry alone
is not yet a sufficient classifier.  This is where externally reusable
leverage/auction information is likely to have the highest value:

- open-interest change and long/short positioning from Binance futures metrics;
- spot versus perpetual leadership;
- taker-flow exhaustion from aggregate trades;
- funding/premium and broad-market residual structure;
- post-impulse acceptance versus exhaustion.

The next state study should distinguish the all-symbol continuation loss from
the all-symbol reversal event before adding another price-only threshold.

## Source-horizon handoff defect

At 2025-12-07 23:59 UTC, a new opposite-direction 2-sigma event appeared exactly
when the position opened at 19:59 reached its 240-minute source horizon.  The
strategy first managed the open position and returned, so it never routed the
new completed-boundary signal.  All three candidates at the new boundary later
had positive diagnostic paths of roughly +0.61R to +0.76R.

This is a control-flow defect in the one-slot adaptation, not proof that the
account should hold two positions.  A causal repair is possible:

1. observe and freeze the newly completed boundary decision;
2. close the old position at its source horizon;
3. only after the account is flat, submit the frozen new decision on a later
   minute;
4. expire it quickly if flattening is delayed;
5. keep the two boundaries grouped correctly rather than counting simultaneous
   symbols as separate trades.

This repair should be tested independently from the market-state filter.

## Shadow limitations

For the eight executed episodes, the diagnostic shadow differed from actual
Nautilus R by about 0.104R mean absolute error.  Actual fills, fees, slippage,
quantity and account state remain authoritative.  Shadow results are suitable
for locating selector, collision and slot problems, not for claiming account
performance.

## Frozen next experiment

A two-axis short experiment is justified on a fresh-to-this-policy interval:

- arbitration: current highest absolute z-score versus least absolute
  qualifying z-score;
- source-horizon control flow: current skip versus deferred flat-account
  boundary handoff.

The four combinations should use the same structural stop, transient-management
state machine, cost assumptions, 3% current-NAV planned loss and one global
slot.  Results must be read at boundary and account-path level.  The experiment
is informative even if all four are negative because it identifies whether the
loss comes from market-event state, symbol arbitration, slot handoff or normal
probabilistic variation.

No long validation is warranted until this specialist preserves its strong
payoff while increasing independent opportunities and rejecting continuation
states.
