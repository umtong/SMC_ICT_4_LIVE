# Winner15m source-fidelity anatomy decision

This is a causal anatomy result, not a binary gate.  The 2025-02-27 through
2025-03-17 data interval, including the 2025-03-03 through 2025-03-09 entry
window, is development data.

## What the source-faithful replay repaired

The prior project adaptation had mixed the public entry idea with several
unlabelled changes.  This replay restored the source's 200 completed 15-minute
startup candles, evaluated a true source condition on every completed source
candle, retained enough one-minute history to support that startup, allowed the
public management schedule to operate beyond six hours, used warm-up and
run-off data, and required an end-flat one-slot account.

The final account was flat, there were no active orders, and closed position
rows matched completed scenarios.  This removes the earlier open-position
accounting error.

## Raw trades and independent causal episodes

The one-slot replay completed 44 raw trades over 37 continuous source-condition
episodes:

- 26 wins and 18 losses;
- 59.09% raw win rate;
- gross-profit/gross-loss factor 0.770;
- mean cost-after result -0.0655R per raw trade;
- NAV 100,000 -> 90,693.78 USDT;
- -9.306% total return;
- 12.02% maximum drawdown.

Raw source-candle re-entry must not be reported as independent opportunity
frequency.  Grouping re-entries back into their continuous causal conditions
produced 37 episodes, 19 positive and 18 negative, with an episode-level PnL
factor of approximately 0.800.  The source-faithful result therefore remains
negative after removing raw-trade count inflation.

## Re-entry was not the failure engine in this interval

Seven entries occurred while the same source condition remained true after a
previous trade had closed.  They produced five wins, two losses, approximately
+0.158R per trade and a PnL factor near 1.43.  The 37 first entries produced 21
wins, 16 losses, approximately -0.155R per trade and a factor near 0.67.

This does not prove that persistent re-entry is generally good.  It does prove
that mechanically collapsing every continuous source condition to one trade
can discard useful monetization while leaving the original false positives
untouched.  The final system should count the condition as one independent
causal episode while allowing a causally justified re-entry policy inside that
episode if the auction state is renewed.

## Profit and loss engines

Every one of the 26 winners completed the public trailing path.  The other 18
trades never activated and completed that path and lost through the hard-stop
branch.  No ROI exit was responsible for the profit engine in this replay.

The approximate payoff geometry was:

- average winner: +0.528R;
- average loss: -0.923R;
- win rate required for breakeven at that geometry: about 63.6%;
- observed win rate: 59.1%.

The strategy is not missing a wholesale directional engine.  It is only a few
correctly separated false-positive episodes away from breakeven in this
specific development window.  However, a generic break-even or immediate
five-minute sign rule is not justified: prior episode replay showed substantial
overlap in early winner and loser paths, and such rules remove eventual trailing
winners together with losses.

## Asset and direction heterogeneity

The same rule behaved differently across latent state:

- SOL shorts: 13 trades, 10 wins, mean about +0.160R, factor about 1.71;
- XRP longs: 3 trades, 3 wins, mean about +0.559R;
- BTC longs: 5 trades, 1 win, mean about -0.396R;
- XRP shorts: 4 trades, 1 win, mean about -0.623R.

These cells are too small to hard-code symbol/direction rules.  They are evidence
that the source threshold alone does not identify the market state in which the
trend leg has remaining space.  A higher-level state/router must explain the
difference using observable market structure, broad-market leadership,
relative extension, spot/futures participation or regime—not the instrument
name learned from this week.

## Code-path conclusion

The source itself was not faithfully reproduced by the earlier 39-candle,
transition-only, six-hour adapter.  Conversely, faithfully restoring source
availability did not make the one-slot four-major account profitable.  Both
facts matter:

1. the public rule contains a repeatable trailing winner engine;
2. the project adapter needs a state selector and arbitration policy that
   preserves remaining auction space and rejects hard-stop states;
3. persistent source truth and independent causal-episode counting are
   different concepts and must remain separately reported;
4. long validation would add no useful information until that state separation
   is demonstrated on short untouched intervals.

The next work is therefore not a threshold sweep or another gate.  It is (a)
the already frozen one-slot arbitration experiment after fixing the score-path
implementation error, and (b) a separately frozen regime/participation
adaptation, with each contribution inspected at episode level before combining
them.
