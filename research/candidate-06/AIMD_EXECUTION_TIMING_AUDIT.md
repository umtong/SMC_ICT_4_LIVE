# AIMD Signal-Submission Timing Implementation Audit

## Classification

The first valid AIMD campaign produced nine completed causal signals and no
trades. Every signal was rejected only after the generic strategy waited for a
further completed one-minute bar and then recomputed entry drift and net reward
at that later close.

This is classified as an **implementation-contract error**, not yet a failure of
the AIMD market thesis.

## Root cause

The scenario engine already requires the following completed sequence:

1. profile A and profile B are complete;
2. value and POC migrate with directional inventory evidence;
2. a later counter-flow retest remains outside old value;
4. a separate later one-minute resumption bar breaks that retest structure.

At the close of step 4 all facts used by the signal are observable. The generic
candidate strategy nevertheless stores the signal, observes one more complete
minute, and submits at that later minute's close. This changes the declared
scenario into `response -> extra unmodelled minute -> entry`. It also lets that
extra minute consume the objective or erode reward/risk before submission.

NautilusTrader's documented bar execution model settles market orders submitted
from `on_bar` at the current bar close, without retroactively traversing that
bar's high/low path. Submitting after the completed response is therefore the
causal bar-backtest representation of a market order sent immediately after the
response close. The existing fee and one-tick adverse slippage assumptions
remain in force.

## Controlled repair

A new logic field is introduced:

- `NEXT_COMPLETED_BAR`: exact legacy behavior and default for all old configs;
- `ON_SIGNAL_CLOSE`: submit after the already-completed signal bar is recorded.

Only AIMD declares `ON_SIGNAL_CLOSE`. The state machine, data, profiles,
thresholds, stop, target, fees, slippage, risk sizing, fill model, weeks and
gates are unchanged. The same first BTC week is rerun.

## Why this is not result-driven entry optimization

No alternative price, retracement fraction, limit level or holding period is
searched. The repair removes a generic extra observation that was absent from the
predeclared AIMD state sequence. The old delayed evidence remains in Git
history for exact comparison.

## Decision rule

- registration, syntax, timing-source, data, Nautilus API or output-contract failure:
  implementation error; fix only the defect and repeat the same week;
- valid first-week metrics but fixed gates fail: AIMD logic failure; interpret
  the already-declared delta/POC/flow ablations and discard without tuning;
- first-week full gate pass: lock the exact config and run the two sealed weeks;
- all three weeks pass: authorize long evaluation only.
