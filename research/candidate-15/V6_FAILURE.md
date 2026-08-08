# Candidate 15 V6 failure: residual ownership was necessary, not sufficient

## Audited result

V6 changed only information ownership: after an exactly three-market response,
only the sole excluded market could submit a fresh post-state continuation leg.
The V5 detector, entry, protected stop, live external target, 3% current-NAV
risk sizing, realistic maker/taker costs and one-global-slot Nautilus execution
were retained.

Across six exposed seven-day development intervals:

- weekly-reset NAV multiple: `1.1161341935`;
- daily geometric growth: `0.2627901848%`;
- 23 closed trades;
- 5 wins / 18 losses;
- win rate: `21.7391%`;
- payoff ratio: `6.5924`;
- active intervals: `6 / 6`;
- closed-trade path maximum drawdown: `31.2510%`;
- accepted-market continuation plans rejected: `304`;
- residual-route violations: `0`.

Residual ownership therefore reversed V5's negative aggregate growth and
preserved opportunity breadth, but it failed the unchanged accuracy, drawdown,
growth-concentration and execution-safety gates. V6 is rejected as a complete
system.

## Causal/path diagnosis

The V6 source and all six intervals were reproduced under NautilusTrader, then
completed event, plan, opening-order, position and one-minute paths were joined
for diagnosis only. The diagnostic did not alter execution.

- 51 residual plans were submitted;
- 23 filled;
- convergence parity was still ahead at entry for 16 fills;
- 11 of those reached parity before the original stop;
- parity itself usually supplied less than one costed planned-loss unit and was
  not a viable final take-profit objective;
- three plans were submitted one minute after a state refresh and all three
  lost;
- many losing trades produced material favorable excursion before eventually
  stopping at a distant external objective path;
- E05/E06 also contained protective stop rejection chains after the parent fill,
  making the affected evidence a safety failure despite immediate fail-close.

## Two implementation/logic errors discovered

### 1. Refreshed ownership retained the old activation timestamp

A fresh common-flow response replaced `accepted_symbols`, origins and ownership
but retained the first activation timestamp. A five-minute leg that had begun
before the refresh could therefore consume the newly refreshed state. The
receiver evidence and the execution leg did not belong to the same causal
auction sequence.

V7 treats every accepted refresh as a new effective evidence boundary. A
completed receiver bar whose start is at or before that boundary is no-trade.

### 2. Receiver displacement self-normalized its own ATR

Sender impulses were compared with ATR from prior completed five-minute bars.
The inherited receiver engine appended the current five-minute true range before
computing the displacement ratio. Large receiver bars therefore enlarged their
own denominator and were not comparable with the sender evidence.

V7 evaluates the receiver leg first and appends its true range afterward.

## Structural redesign

V6 allowed any fresh residual-market continuation after ownership was assigned.
V7 instead models bounded information transfer:

```text
three sender markets demonstrate common price/flow conversion
                         ↓
sole excluded market is measurably behind at confirmation
                         ↓
new evidence timestamp owns all later receiver bars
                         ↓
receiver forms fresh MSS/displacement/FVG using prior 5m ATR
                         ↓
receiver displacement is material but weaker than weakest sender
                         ↓
convergence parity remains unconsumed and <1 costed R ahead
                         ↓
passive retracement with same-leg invalidation and live external target
```

The sender-relative body interval `[0.5, 1.0)` and parity interval `(0, 1.0) R`
encode partial-but-material delivery. They are exposed mechanism-development
boundaries and cannot establish success. V7 retains the unchanged development
gate and must still earn independent confirmation before any success claim.
