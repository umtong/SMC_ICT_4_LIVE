# EasyChart core trading contract

This file is the controlling execution policy for the canonical candidate. It
is not a parameter-search surface.

## Required

- One entry for the selected causal episode.
- One full-position protective stop.
- One full-position take-profit order.
- Entry, stop and target are fixed before order submission.
- Account loss at the planned stop is sized to **3% of current NAV**, including
  the configured fee, slippage and funding reserve used by the sizing model.
- A plan is tradable only when its **pre-entry gross reward/risk is at least
  1.0R**.
- The four symbols share one continuous account and one global position slot.
- There is no artificial limit on the number of valid independent trades.

## Forbidden in the canonical candidate

- Partial profit-taking.
- Partial stopping or staged stop-outs.
- Moving a stop to breakeven after entry.
- Daily loss limits or daily risk governors.
- Forced exits based on elapsed holding time, including a 24-hour rule.
- Dynamic early exits from a newly appearing opposing OB or other post-entry
  signal.
- Trade-count quotas, cooldown quotas or a maximum-trades-per-day rule.

The only non-stop/non-target flatten is the mechanical close at the end of a
finite backtest evaluation. It is an accounting boundary, must be labelled as
such, and is not a strategy rule.

## Source interpretation

The supplied EasyChart material lists partial exits and breakeven management as
optional methods in some examples. It also explicitly allows taking the full
position at a target. The canonical automated system deliberately selects the
simpler full-exit option. Optional source variants are not permission to add
extra management layers without an explicit project decision.

No supplied source establishes a universal 24-hour forced exit, and no such
rule belongs in the canonical candidate.

## Research boundary

Research may improve market-state recognition, structure construction,
scenario causality, entry location, invalidation geometry and target selection.
It may not change this execution contract implicitly. Any proposed contract
change requires a separate branch, a direct comparison, and explicit approval
before it can become canonical.
