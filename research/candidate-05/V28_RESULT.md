# Candidate 05 v28 result — completed-session external liquidity

## Decision

**Restore v26 as the active baseline. Retain v28 only as a structural-context experiment.**

v28 was neither a profitable new scenario nor a damaging logic failure. Across
all three frozen BTC weeks it produced the same orders, fills, positions, daily
returns, total returns and drawdowns as v26. Its only effect was to classify or
strengthen some existing five-minute liquidity pools with the high and low of a
fully completed four-hour UTC activity session.

The identity result is the controlled ablation: removing v28's only new variable
returns exactly to v26. No threshold tuning or second ablation is warranted.

## Hypothesis tested

The complete v26 local detector and execution system remained unchanged. v28
added two observations at every completed four-hour UTC session boundary:

```text
completed session high -> external buy-side liquidity
completed session low  -> external sell-side liquidity
```

A clock boundary was never an entry signal. A level still had to pass the same
penetration/activity, absorption, tail-flow, current-depth, CHoCH, frozen-target,
cost, slippage and current-NAV 3% risk logic. A session extreme within the
existing 0.10 ATR pool-merging tolerance strengthened the existing local pool
instead of creating a score or risk multiplier.

## Implementation integrity

- `smc4 doctor` passed in the fixed research image.
- 95 unit and contract tests passed.
- NautilusTrader owned every order, fill, position, fee and NAV result.
- There were no order rejections, order denials or liquidations.
- Maximum observed open positions and simultaneous entry intents remained one.

## Frozen-week evidence

Week 1 workflow run `31109827982`, commit
`e670b92734bbe0742c2c2a5bffe4dbd84eb0abe5`.
Weeks 2 and 3 workflow run `31110122224`, commit
`37b808a2cef88bcfb468f797ba84401d83ebee7f`.

| Frozen BTC week | Total return | Geometric daily growth | Trades / wins | Active days | Maximum drawdown |
|---|---:|---:|---:|---:|---:|
| 2023-07-09..15 | +8.405430% | +1.159644% | 7 / 6 | 5 | 3.084842% |
| 2024-01-15..21 | +8.910630% | +1.226857% | 4 / 4 | 3 | 3.430344% |
| 2023-09-08..14 | +3.062632% | +0.431883% | 3 / 3 | 3 | 1.571924% |

Every value above is identical to the v26 baseline.

## External-session diagnostics

Each weekly build/evaluation run completed 53 four-hour sessions and emitted 106
session-extreme observations.

| Week | New standalone pools | Merges into existing pools | Merge share |
|---|---:|---:|---:|
| 1 | 17 | 89 | 83.96% |
| 2 | 17 | 89 | 83.96% |
| 3 | 14 | 92 | 86.79% |

The high merge share is useful evidence: a large portion of completed-session
extremes was already represented by Candidate 05's causally confirmed local
five-minute liquidity structure.

In Week 1 one submitted path was attributed to a completed-session low, but the
v26 evidence contained the same trade at the same time with the same entry,
structural stop and target through a local five-minute pool. The external level
therefore improved causal attribution but did not create an independent trade.

## Interpretation without binary generalization

The result does **not** show that activity-session liquidity is unimportant. It
shows that, under this implementation and these three frozen weeks, completed
four-hour extremes were mostly redundant with the existing local pool universe
and did not increase executable opportunities.

The result also does **not** justify making session levels exclusive or replacing
five-minute pools. The earlier v3 wholesale higher-timeframe replacement reduced
Week-1 activity to three trades with a single winner dominating performance.

## Useful component retained

Completed-session provenance is useful as an explanatory annotation for an
already confirmed pool. It may later help diagnose whether a scenario consumed
internal or external liquidity, but it should not remain in the active candidate
until it changes a decision with independently validated positive expectancy.

## Largest performance driver

Performance remained entirely driven by the existing v26 scenario paths:

- sponsored CHoCH participation,
- confirmed CHoCH retrace,
- confirmed second touch,
- reset-and-reaccelerated balance acceptance.

The added session clock generated no incremental alpha and no incremental loss.
Keeping it active would therefore add state, events and pool churn without an
observed decision benefit. v26 is restored as the active baseline while the v28
code and evidence remain reproducible in the branch.
