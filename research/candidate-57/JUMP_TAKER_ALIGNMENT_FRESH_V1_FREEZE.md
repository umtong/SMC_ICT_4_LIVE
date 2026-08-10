# 4h jump reversal — frozen peer taker-alignment state experiment

This experiment is frozen before reading the 2026-04-01 through 2026-04-14
market result.  It is not a parameter search and it is not a binary gate.  It
tests one external market-state distinction extracted from Binance Vision
futures metrics and the prior 2025-12 causal audit.

## Development observation that motivates the rule

At each audited completed 4-hour boundary, Binance's five-minute futures metrics
were joined strictly as-of: only the latest snapshot at or before the boundary
was available to the decision.

The 2025-12-09 all-symbol failed short-reversal boundary retained taker buy/sell
ratios above one in BTC, ETH and XRP; aggressive flow had not flipped against
the upward impulse.  By contrast, the strong 2025-12-07 short-reversal boundary
had ratios below one across all four majors, and the positive opposite-direction
boundary four hours later had ratios above one across all four.  Across the nine
development boundaries, requiring at least three of four peer ratios to point
in the proposed reversal direction would have removed the all-symbol -1R
continuation event while retaining the large reversal episodes.  It would not
have removed every loss and is not claimed to identify every winner.

This observation is now development data.  The exact rule below is frozen once
and must not be tuned from the fresh result.

## Fresh interval

- 2026-04-01 through 2026-04-14 UTC
- BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- Binance Vision USD-M futures daily `metrics` archives downloaded before replay
- latest metric row at or before each completed 4-hour boundary, maximum age 10
  minutes
- one global pending entry or position
- current-NAV planned-loss budget 3%
- NautilusTrader matching/accounting and project costs

The interval is also used by the separately frozen arbitration × handoff
experiment.  Both experiments were specified before reading the interval.
After either result is opened, the interval is development data for all
Candidate-57 jump changes.

## Signal and execution held constant

Both cells preserve:

- completed 4-hour causal return, prior 18 completed returns, absolute z >= 2.0;
- reversal direction;
- whole-impulse structural stop;
- 20% emergency target;
- 240-minute source horizon;
- transient protection arm +0.4R, escape +1.0R;
- current source maximum-z simultaneous-symbol arbitration;
- no deferred boundary handoff;
- one global account slot and realistic costs.

## Two frozen cells

### `source_without_taker_filter`

The existing source signal with no metrics-based state rejection.

### `peer_taker_alignment_3of4`

For a proposed long reversal, at least three of the four latest peer
`sum_taker_long_short_vol_ratio` values must be strictly greater than 1.0.
For a proposed short reversal, at least three must be strictly less than 1.0.
All four snapshots must be available and no older than 10 minutes.  Otherwise
the completed boundary is `UNRESOLVED / NO TRADE`.

The ratio is used only as an observed state transition after the completed
price impulse.  It is not reused to define the price jump, stop or target.
No alternative majority, threshold, OI condition or symbol-specific exception
will be selected after seeing this interval.

## Required interpretation

Preserve every source boundary, accepted and rejected decision, the four peer
snapshots and their exact as-of ages.  Compare:

- whether rejected boundaries were continuation states or favorable reversals;
- whether large source winners survive;
- whether the rule merely raises win rate by deleting payoff;
- completed independent boundaries, not raw simultaneous symbol signals;
- NAV, R geometry, drawdown and account-slot path;
- missing/stale metrics versus genuine state rejection;
- implementation behavior, logic errors and ordinary probabilistic losses.

If no source boundary is rejected, the interval is under-informative.  If the
filter removes both losses and large winners, the distinction is not adequate.
If it helps, it remains only one state component and must move to a different
untouched interval before integration with arbitration or boundary handoff.
