# Candidate 60 — frozen quarter-hour synchronized order-flow diagnostic

## Economic mechanism

This is not a calendar-anomaly trade and not a technical-indicator pattern.
Electronic markets share standardized one-, five- and fifteen-minute clocks
across exchange APIs, charting systems, execution schedules and automated
strategies. When many agents release marketable orders at the same boundary,
their execution becomes temporally coordinated. Aggregate aggressive order flow
can then be partly predictable and can transmit information or inventory pressure
beyond the opening burst.

A July 2026 study of six Binance USDT perpetual contracts reports that activity
peaks in the first ten seconds of quarter-hour minutes, that the arriving trades
have a behavioral signature consistent with algorithmic participation, and that
quarter-hour opening order imbalance predicts four-to-twelve-hour returns. The
paper is an external discovery signal, not project evidence. Candidate 60 tests
the mechanism on later data and on its own four-symbol universe before any
strategy is built.

## Reused data solution

Candidate 05 already provides checksum-verified Binance Vision download and a
chunked aggregate-trade parser. It infers aggressor direction from
`isBuyerMaker`, aggregates the first ten seconds of every minute, and exposes:

- opening-ten-second total notional;
- opening-ten-second signed notional;
- opening-ten-second trade count;
- complete one-minute price bars.

This diagnostic reuses that implementation. It does not create a matching,
portfolio or account engine. Any policy promoted from this diagnostic must later
run through the project's NautilusTrader one-slot continuous account.

## Frozen causal observation

For every UTC minute `t`:

`opening_flow(t) = signed_notional_first_10s / total_notional_first_10s`

- buyer-taker notional is positive;
- seller-taker notional is negative;
- zero-flow minutes are unresolved;
- the diagnostic direction is `sign(opening_flow)`;
- the conservative decision price is the close of minute `t`, after the ten
  second observation is fully known;
- future log returns are measured from that close to 60, 240, 480 and 720
  minutes later.

No magnitude threshold, volatility threshold, symbol exception, time-of-day
exception or result-fitted normalization is used.

## Clock phases fixed before results

- target phase: UTC minutes `00, 15, 30, 45`;
- placebo phase: UTC minutes `07, 22, 37, 52`;
- the seven-minute shift preserves four equally spaced observations per hour but
  removes the shared quarter-hour boundary;
- the cross-symbol selector proxy chooses, at each boundary, the one symbol with
  the largest absolute opening imbalance; ties use the fixed project symbol
  order BTC, ETH, SOL, XRP;
- an independent four-hour sample retains only hour `00, 04, 08, 12, 16, 20`
  target boundaries (and the corresponding minute-07 placebo boundaries).

The selector and independent sample are diagnostics only. They do not claim
fills or NAV.

## Frozen intervals

Development:

- event dates: **2026-07-20 through 2026-07-26 UTC**;
- one following day is downloaded for forward outcomes.

Conditional policy-fresh:

- event dates: **2026-07-27 through 2026-08-02 UTC**;
- one following day is downloaded for forward outcomes;
- it is consumed only if the development evidence supports the declared
  mechanism.

Neither interval is used to choose a threshold or horizon. Once inspected, an
interval is development data for later policy changes.

## Development interpretation fixed in advance

Development authorizes the predeclared fresh diagnostic only when:

1. data are complete and checksum verified for all four symbols;
2. the target phase has nonzero-flow observations in all four assets;
3. the day-balanced signed return is positive at at least two of the three
   medium horizons: 240, 480 and 720 minutes;
4. at 240 minutes, at least three of four assets have positive mean signed
   returns;
5. the one-symbol selector proxy and its non-overlapping four-hour sample both
   have positive 240-minute mean signed returns;
6. the target phase's 240-minute day-balanced mean exceeds the shifted placebo;
7. no result is created by changing the phase, direction, asset set or horizon.

A policy-fresh replication authorizes construction of a NautilusTrader scenario
family only. It is not trading-system performance evidence. Failure closes this
exact raw-sign quarter-hour mechanism without searching imbalance cutoffs,
calendar subsets or alternative phase shifts on these dates.
