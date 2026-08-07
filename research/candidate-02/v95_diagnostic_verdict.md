# Candidate-02 v95 diagnostic verdict

## Decision

`DISCARD_AFTER_PRECOMMITTED_SINGLE_ABLATION`

The result is a logic/frequency failure, not a NautilusTrader, data, risk-sizing, or execution-pipeline failure.

- Locked first BTC week: `2025-10-06T00:00:00Z` to `2025-10-13T00:00:00Z`
- Performance engine: NautilusTrader `1.230.0`
- Risk fraction: current account NAV × `3%`
- Central, adjacent retrace, and adjacent maturity variants: `0` signals, orders, fills, and trades
- Diagnostic workflow run: `31147692877`
- Diagnostic source commit: `31dafbe0bec0011be1fd155d75b32f6b547efab1`
- Diagnostic artifact digest: `sha256:d85c2f28270117c1a91e2d8b0ab1a40bb282208c6eda4800ca5fa0c9cf6eebee`

## Implementation errors separated and corrected

Two CI boundary errors initially prevented the causal funnel from running after a valid weekly rejection:

1. the workflow treated runner exit code `1` as an infrastructure failure even though it represented a normal research rejection;
2. the workflow accepted only the older decision label `FIRST_WEEK_REJECT`, while the immutable runner emitted `FIRST_WEEK_REJECT_OR_PRECOMMITTED_ABLATION`.

Neither correction changed the strategy, week, source payload, costs, risk, or parameters. The same locked week was replayed after each correction. The final diagnostic job completed successfully.

## Central funnel

Qualification:

- swing candidates: `262`
- survived 480-minute maturation: `68`
- defense approach seen: `5`
- defense rejection confirmed: `4`
- qualified levels: `4`

Scenario:

- minutes with an active level: `24 / 10,080`
- unique active levels: `2`
- one-sided first breaches: `2`
- event-extension pass: `0`
- classification complete: `0`
- production signals: `0`

The central hypothesis was structurally too sparse before common spot-perpetual acceptance could even be tested.

## Prospectively fixed single ablation

Ablation: remove only prior defense-memory qualification. All other logic remained unchanged.

Qualification:

- survived maturation: `68`
- qualified levels: `68`
- active minutes: `10,080 / 10,080`
- unique active levels: `52`

Scenario:

- one-sided first breaches: `28`
- event extension and turnover pass: `18`
- classification complete: `18`
- outside-close pass: `8`
- spot/basis common-acceptance pass: `7`
- displacement/FVG pass: `4`
- FVG touches: `6`
- midpoint rejection: `2`
- retrace-flow pass: `1`
- target pool nonempty: `0`
- target geometry/cost pass: `0`
- production signals: `0`

Removing defense memory repaired opportunity formation but exposed a second independent structural failure: the nearest-intact-swing target contract produced no eligible external liquidity pool for any surviving setup. Because the single precommitted ablation still generated zero production signals, no NautilusTrader PnL run was necessary for that ablation.

## What worked

The common-acceptance chain was not wholly invalid. Without defense memory, seven cross-market acceptance events and four displacement/FVG events survived. This supports retaining cross-market acceptance and displacement as confirmation layers, but not the mature defended swing boundary or nearest-intact-swing target architecture.

## What failed and must not be repeated

- requiring both long maturation and remembered defense collapses active opportunity time;
- replacing mechanical boundaries with mature swings does not by itself improve frequency;
- an entry architecture cannot depend on a target-pool definition that is empty after the setup has formed;
- adding filters to this family would worsen the demonstrated bottleneck;
- parameter searches around maturity/retrace are not justified because all locked adjacent variants also produced zero signals.

## Next research action

Proceed to the already prospectively locked v103 endogenous turnover-clock order-flow regime candidate. It removes wall-clock and static-target dependence, classifies retained impact versus absorbed flow in non-overlapping market-time packets, and lets the packet geometry itself define invalidation and objective. v103 must be evaluated on its locked first BTC week with NautilusTrader before any redesign or parameter change.
