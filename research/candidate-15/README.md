# Candidate 15 V4 — Persistent Cross-Market Initiative

Candidate 15 V1–V3 produced three useful corrections but not a viable system:

- V1 exposed permanent reuse of a resolved local auction state.
- V2 converted that state into a short causal decision lease and exposed a stop
  fallback inside the original sweep invalidation.
- V3 enforced scenario-terminal sweep invalidation, but its predeclared screen
  still produced only one trade and that trade lost because the upper
  cross-market role router selected an already depleted XRP continuation.

`V1_FAILURE.md`, `V2_FAILURE.md` and `V3_FAILURE.md` preserve those results.

V4 does not relax the failed family's filters. It quarantines all V3 SCDAM plans
and `SESSION_I7`, retaining the core engines only as causal external-liquidity
pool observers. One independent family is evaluated:

```text
first UTC quarter-hour common-flow event
                 ↓
             candidate only
                 ↓
second distinct same-direction event within 4h
                 ↓
      persistent initiative ACTIVE
                 ↓
independent post-activation 5m MSS + displacement
                 ↓
 strict three-candle FVG + passive CE retracement
                 ↓
protected-swing/opposing-bar stop + live external target
                 ↓
         one global portfolio slot
```

## State contract

A common-flow event requires at least three of BTC, ETH, SOL and XRP to agree in
directional body, displacement and signed taker-flow proxy on the first completed
five-minute interval of a UTC quarter hour. The first event is never traded. A
second distinct same-direction event activates a four-hour state.

The state terminates on:

- an opposite common-flow event;
- majority close reacceptance through the latest confirming origins;
- four-hour expiry.

Only a new five-minute leg completed after activation may generate a plan. Entry
is post-only at the strict FVG consequent encroachment. Stop, invalidation and
target belong to that new leg. The target is the next causally confirmed live
completed-4H or previous-day external pool.

## Execution invariants

- NautilusTrader owns orders, fills, fees, margin, positions and NAV.
- Quantity uses current whole-account NAV and 3% planned loss.
- The four markets share one pending-entry/open-position slot.
- Maker/taker fees and inherited execution semantics remain active.
- Any rejected protective child after parent fill triggers fail-closed flattening
  and remains an engine error.
- Core V3 and session plans are explicitly terminally rejected, not silently
  omitted.

## Development protocol

E01–E06 are already exposed diagnostic weeks spanning adverse, strong,
frequency, mid-regime and recent conditions. They can reject or improve the
mechanism, but cannot support a success claim. A promising result must first meet
all declared activity, breadth, growth, win-rate, payoff, drawdown,
concentration and safety gates before any newly predeclared confirmation screen.

```bash
for interval in E01 E02 E03 E04 E05 E06; do
  bash research/candidate-15/run_week.sh "$interval"
done
python research/candidate-15/aggregate.py
```

The generated `RESULT.md` and `aggregate.json` report the exposed Nautilus screen.
