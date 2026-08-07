# OICB research ledger

## Predeclared hypothesis

A completed five-minute open-interest contraction is a deleveraging shock, not a trade. A later completed one-minute response must bifurcate it into:

- exhaustion: the shock midpoint is reclaimed with opposite body and aggressive flow;
- continuation: the midpoint is defended on a retest and a separate same-direction response resumes.

The full candidate uses a prior-only robust OI contraction gate. Fixed ablations remove only OI or only shock-time flow alignment. No performance-dependent risk multiplier, symbol rule, session filter, or parameter rescue is allowed.

## Fixed validation and execution

- NautilusTrader only;
- BTC first frozen week `2024-02-26`;
- unchanged fees, slippage, fill model and 3% NAV planned-loss sizing;
- full candidate alone is eligible for promotion;
- weeks `2024-09-23` and `2024-04-22` open only if the full first-week gate passes;
- missing or stale OI rows cause abstention, never forward fill.

## Decision rule

Parser, schema, clock, runner, or order-lifecycle failures are implementation errors and require the identical week to be rerun. Valid Nautilus metrics below the gate are logic failure. The fixed ablations identify whether OI or flow alignment added value; absent a structural path the family is discarded.
