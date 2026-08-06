# V13 sequential event-auction failure analysis

## Disposition

`candidate-03-nt-lvcfr-v13-sequential-event-auction` is rejected as a
complete candidate. Two state families are retained as validated components:

- `FIRST_BREAK_CHOCH_REVERSAL`;
- `MEASURED_ACCEPTANCE_CONTINUATION`.

The `MIDPOINT_FAILURE_CHOCH_REVERSAL` state is rejected.

## Implementation versus logic

The first development-week failure was logical rather than an implementation
failure.

- `smc4 doctor` confirmed Python 3.13.5 and NautilusTrader 1.230.0.
- All causal sequencing tests passed: intrabar touches could not confirm a
  break; missing minutes invalidated a sequence; the first completed structural
  outcome won.
- Twenty-three native positions opened and closed normally with no entry
  rejection or unfinished exposure.
- The fixed current-NAV 3% planned-loss quantity, fees, impact, funding,
  single-slot portfolio and native Portfolio NAV path were unchanged.

## Full V13 first development week

Week: `2024-01-08`

| Metric | Result |
|---|---:|
| V1 source events | 30 |
| V13 signals | 28 |
| executed independent episodes | 23 |
| winners / losers | 7 / 16 |
| win rate | 30.43% |
| final NAV | 98,996.25 USDT |
| net return | -1.0037% |
| daily geometric growth | -0.1440% |
| mean episode PnL | -43.64 USDT |
| maximum drawdown | 14.12% |

## State attribution

| V13 state | Executed trades | Winners | Native PnL |
|---|---:|---:|---:|
| `FIRST_BREAK_CHOCH_REVERSAL` | 5 | 3 | +3,479.33 |
| `MEASURED_ACCEPTANCE_CONTINUATION` | 6 | 2 | +3,950.76 |
| `MIDPOINT_FAILURE_CHOCH_REVERSAL` | 12 | 2 | -8,433.83 |

The sequential design correctly repaired V12's immediate same-side BOS error:
measured acceptance and first opposite-side CHoCH were both profitable. The
remaining failure came from treating the event midpoint as a sufficient failed
break confirmation. A midpoint close frequently represented an ordinary
retracement inside the post-liquidation auction rather than a durable CHoCH.

## Required one-variable ablation

Removed core variable:

`event_midpoint_close_alone_confirms_failed_break_CHOCH`

Only `MIDPOINT_FAILURE_CHOCH_REVERSAL` was removed. First-break CHoCH and
measured-acceptance continuation signals, entries, stops, targets, costs,
funding, fixed 3% risk and the native NautilusTrader path were unchanged.

| Metric | Full V13 | Remove midpoint-failure state |
|---|---:|---:|
| signals | 28 | 14 |
| executed episodes | 23 | 12 |
| win rate | 30.43% | 50.00% |
| final NAV | 98,996.25 | 112,563.42 |
| net return | -1.0037% | +12.5634% |
| daily geometric growth | -0.1440% | +1.7050% |
| mean episode PnL | -43.64 | +1,046.95 |
| profit factor | 1.012 | 2.440 |
| maximum drawdown | 14.12% | 7.99% |
| gate | fail | pass |

The ablation establishes that the two retained V13 states form a real positive
component on the first week.

## Why V13 is still discarded as a complete candidate

The retained state set does not supply enough opportunity across the next
frozen development context. Causal schedule inspection of `2025-06-23` leaves
only four signals after removing midpoint failures: three first-break CHoCH
reversals and one measured-acceptance continuation. Running a full native gate
cannot repair a deterministic signal-count failure below the minimum eight
independent episodes.

Therefore the correct structural improvement is not to tune midpoint distance,
waiting time or target. The retained alpha must be embedded only where the
stronger V11 scenario router has explicitly declined an event.

## Components retained for V14

1. **First opposite-side completed break as CHoCH.** This state was positive in
   both V12 and V13 controlled decompositions.
2. **Measured same-side acceptance.** One full endogenous event-range extension
   was materially better than the first same-side break.
3. **V11 priority.** External acceptance/reclaim, range migration and failed
   reclaim/reacceptance remain the primary states because they already passed
   two full development weeks.
4. **No fallback on same-side break.** Same-side breaks that do not reach a
   validated V11 state remain NO_TRADE rather than being forced into a new
   continuation label.
5. **Native infrastructure unchanged.** All execution and account evidence
   continues through NautilusTrader 1.230.0.

## V14 transition

V14 routes each original event at most once:

```text
V11 emits a validated scenario
    -> use V11, no fallback

V11 emits no scenario
    -> wait for first completed event-range break
       -> opposite original direction: CHoCH fallback reversal
       -> same original direction: NO_TRADE
       -> unresolved: NO_TRADE
```

This adds an independently validated opportunity family without weakening V11's
priority or adding fitted thresholds.
