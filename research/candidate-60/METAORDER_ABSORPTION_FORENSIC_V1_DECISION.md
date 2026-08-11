# Candidate 60 — metaorder absorption forensic V1 decision

## Status

**The nominal absorption-under-load successor is rejected. The dynamic-extreme and objective-consumption findings are preserved. No V3 trade policy is authorized and the reserved fresh interval remains untouched.**

This was an outcome-aware forensic study on the already-consumed `2026-04-13`
through `2026-04-19` development interval. It was not a backtest or an account
claim.

Evidence:

- successful GitHub Actions run: `31524927550`
- evidence commit: `36659ee82f8bed88357e294deb6917bfb1a96425`
- exact V1 events traced: `7`
- outcome groups reproduced exactly:
  - `FAST_TARGET`: `3`
  - `EARLY_STOP_LATER_POSITIVE`: `3`
  - `STOP_NO_LATER_POSITIVE`: `1`
- trace horizon: `120` completed minutes per event
- reserved `2026-08-03` through `2026-08-09`: not consumed

## Question tested

V2 showed that visible price reclaim corrected the intended premature-entry
loss group but generally spent the reachable parent-run VWAP objective before a
trade could be justified. The forensic therefore asked whether a price-leading
state appeared earlier:

```text
parent-direction aggressive flow persists
+ marginal return collapses versus the completed parent run
+ price impact per unit of aggressor flow collapses
+ opposite-side depth replenishes faster than same-side depth
```

This was called `absorption_under_load`. A stricter diagnostic also required two
consecutive parent-flow minutes and lower efficiency. A separate
`counterflow_resilience` landmark required opposite flow plus positive
three-minute book resilience.

The exact V1 parent detector was imported and called directly. Outcome labels
were attached after the causal observations were written.

## Result: the nominal observations do not separate the states

### EARLY_STOP_LATER_POSITIVE

| event | fresh parent extremes | V1 stop | parent VWAP | counterflow resilience | absorption under load | V1 target |
|---|---:|---:|---:|---:|---:|---:|
| BTC 2026-04-13 | 2 | 15m | 5m | 11m | 68m | 91m |
| BTC 2026-04-14 | 4 | 1m | 7m | 28m | 14m | 32m |
| XRP 2026-04-13 | 2 | 26m | 3m | 5m | 105m | 75m |

The proposed price-leading landmark was not actually early. It arrived 14, 68
and 105 minutes after the provisional state. In all three cases the parent-run
VWAP was already reached within 3 to 7 minutes, and no event combined
`absorption_under_load` with usable pre-run-value geometry.

### FAST_TARGET

| event | fresh parent extremes | V1 target | parent VWAP | counterflow resilience | absorption under load |
|---|---:|---:|---:|---:|---:|
| ETH 2026-04-17 | 0 | 24m | 3m | 22m | 6m |
| SOL 2026-04-13 | 0 | 5m | 1m | 6m | 34m |
| SOL 2026-04-14 | 0 | 6m | 1m | 5m | none |

Two fast winners showed the same nominal absorption observation, while the
third reached its objective without it. Thus the observation is neither
necessary for reversal nor specific to the slower premature-stop group.

### STOP_NO_LATER_POSITIVE

The sole non-reversal event, SOL on `2026-04-19`, produced:

- 11 fresh parent-direction extreme updates;
- V1 stop at minute 1;
- counterflow resilience at minute 6;
- parent-run VWAP only at minute 58;
- absorption under load at minute 101;
- no source-target touch within the trace.

It therefore also produced both nominal observations. Their mere presence does
not identify a tradable exhaustion state.

### Deep absorption

`deep_absorption_under_load` occurred in zero of seven events. It cannot support
or refute a trading policy and is too sparse to justify threshold relaxation on
known outcomes.

## What is learned

The useful distinction is not a fixed absorption threshold. It is the evolution
of the parent run after the first marginal-decay observation:

1. **Fast completed reversal:** zero fresh parent extremes and value recovery in
   one to three minutes.
2. **Premature V1 entry with later reversal:** two to four fresh parent extremes
   before the move ultimately reverses.
3. **Persistent accepted parent auction:** eleven fresh parent extremes and no
   later positive reversal inside the fixed horizon.

The number of future extreme updates separates these known paths, but it is an
outcome and cannot be used directly. Any successor must estimate a causal
termination hazard or parent-auction survival probability from information
available at each completed minute.

The forensic also rules out a tempting shortcut: opposite flow plus book
resilience occurred at minutes 5 to 28 in all three groups, including the
non-reversal. It is not sufficient by itself.

## Why no V3 is built

A V3 made by loosening return-z, impact-per-flow-z, depth-resilience,
consecutive-flow or value-window cutoffs would be parameter fitting on seven
known events. The current representation did not create the predicted causal
separation. Backtesting another nearby threshold stack would therefore be an
unjustified search rather than a falsifiable experiment.

The exact following family is closed:

```text
V1 parent run
→ parent flow remains active
→ standardized return and impact-per-flow collapse
→ opposite-side depth replenishes
→ reverse toward parent/pre-run value
```

Do not tune its z cutoffs, resilience window, pre-run value window, symbols,
direction or horizon on the consumed interval.

## Preserved components

- exact parent-run detector reuse;
- renewed parent-direction extremes reset exhaustion evidence;
- explicit `UNRESOLVED / NO TRADE` and `OBJECTIVE_CONSUMED / NO TRADE`;
- event-path tracing before outcome attribution;
- natural value references and cost-aware geometry checked before performance;
- recognition that fast reversal, delayed reversal and accepted continuation
  are competing stopping-time states;
- untouched August reservation.

## Next action

Do not spend the untouched interval on this family. Move to a different source
of opportunity with greater capacity, while retaining the stopping-time lesson:
a surface pattern is not a trade until the remaining objective and current
auction ownership are both established.

A separate promising external-source family already produced twelve exact
Nautilus trades across three 28-day windows. Its loss anatomy is materially
more actionable: source-faithful daily-low reclaim trades with high
cross-sectional absorption under residual sell pressure were positive in five
of six cases, while the largest structural loss occurred without that state.
That family should be adapted and tested as a distinct specialist, not used to
rescue the retired metaorder policy.
