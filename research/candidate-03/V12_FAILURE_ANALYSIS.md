# V12 event-range resolution failure analysis

## Disposition

`candidate-03-nt-lvcfr-v12-event-range-resolution` is rejected as a complete
candidate after its first BTC development week and the required one-variable
ablation. The opposite-side range-break reversal state is retained as a useful
component for the next state-space candidate.

## Implementation versus logic

The failure is logical, not an implementation failure.

- `smc4 doctor` confirmed Python 3.13.5 and NautilusTrader 1.230.0.
- All causal contracts passed: only completed closes could resolve an event
  range; intrabar touches could not create BOS/CHoCH; missing minutes invalidated
  the sequence rather than skipping time.
- Native NautilusTrader submitted and closed 52 orders and 26 positions.
- There were no entry rejections, unfinished positions or portfolio-slot
  violations.
- Every quantity used the current native account NAV and the fixed 3% planned
  loss budget with fees, impact and adverse funding included.

The economic result is therefore admissible evidence against the V12 logic.

## First development week

Week: `2024-01-08`

| Metric | V12 result |
|---|---:|
| V1 source events | 30 |
| causally resolved V12 signals | 29 |
| executed independent episodes | 26 |
| winners / losers | 9 / 17 |
| win rate | 34.62% |
| final NAV | 94,745.60 USDT |
| net return | -5.2544% |
| daily geometric growth | -0.7681% |
| mean episode PnL | -202.09 USDT |
| profit factor | 0.9786 |
| maximum drawdown | 26.59% |
| native orders / positions | 52 / 26 |

## State attribution

| V12 state | Trades | Wins | Native PnL |
|---|---:|---:|---:|
| `EXTERNAL_EVENT_RANGE_FAILURE_REVERSAL` | 4 | 3 | +5,341.52 |
| `INTERNAL_EVENT_RANGE_CHOCH_REVERSAL` | 3 | 1 | +880.75 |
| `EXTERNAL_EVENT_RANGE_EXPANSION_CONTINUATION` | 5 | 1 | -1,132.40 |
| `INTERNAL_EVENT_RANGE_BOS_CONTINUATION` | 14 | 4 | -10,344.28 |

The decisive factor was not the event-range detector itself. It was the binary
interpretation of the first completed break:

- first break opposite the original liquidation direction produced seven
  reversal trades and +6,222.27 USDT;
- first break in the original direction produced nineteen continuation trades
  and -11,476.67 USDT.

Fourteen of the twenty-six positions closed at their initial invalidation. The
first same-side close beyond an event extreme therefore did not establish
acceptance; it frequently represented a stop cascade or liquidity probe that
was subsequently absorbed.

## Required one-variable ablation

Core variable removed:

`same_direction_first_completed_break_is_BOS_continuation`

Every continuation state was removed. Opposite-side CHoCH/failure reversals and
all downstream execution, stop, target, cost, funding, risk and NAV rules were
unchanged.

| Metric | Full V12 | Remove same-side continuations |
|---|---:|---:|
| signals / episodes | 29 / 26 | 7 / 7 |
| win rate | 34.62% | 57.14% |
| final NAV | 94,745.60 | 108,543.60 |
| net return | -5.2544% | +8.5436% |
| daily geometric growth | -0.7681% | +1.1781% |
| mean episode PnL | -202.09 | +1,220.51 |
| profit factor | 0.9786 | 2.4815 |
| maximum drawdown | 26.59% | 5.11% |
| minimum-eight-episode gate | pass | fail: seven episodes |

This ablation establishes a real alpha component but not a complete candidate.
Simply filtering V12 to reversal-only would overfit opportunity scarcity and
would not satisfy the project's requirement for sufficient repeated trades.

## Useful mechanisms retained

1. **The liquidation event range is a useful temporary auction object.** Its
   extremes and midpoint can be observed causally and interpreted without a
   discretionary chart label.
2. **The first opposite-side completed break is informative.** It represents a
   CHoCH against the original liquidation direction and had positive native
   expectancy in the first development week.
3. **The first same-side break is a state transition, not an entry.** It must
   enter a pending state where acceptance and failed-break reversion compete.
4. **Completed-close sequencing is superior to intrabar pattern naming.** It
   prevents wick-only hindsight and maps directly to executable state changes.
5. **The native NautilusTrader infrastructure remains valid.** No backtest or
   accounting engine change is implicated by the failure.

## Structural improvement path

The next candidate does not add a fitted filter. It replaces the failed binary
state transition with a symmetric sequential auction:

1. Define the ten-minute liquidation event range.
2. If the first completed break is opposite the original event direction,
   retain the validated immediate CHoCH reversal state.
3. If the first break is in the original direction, do not enter.
4. Compete two structural outcomes:
   - a completed close one event-range extension beyond the broken extreme,
     representing measured acceptance and continuation;
   - a completed close through the event midpoint, representing failed
     acceptance and CHoCH reversal.
5. The first completed outcome wins; unresolved states expire without a trade.

The extension and midpoint are derived from the event itself, and the waiting
horizon, ATR buffers, targets and risk rules are already frozen project values.
This creates a new state-space candidate rather than tuning V12's losing entry.
