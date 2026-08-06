# Candidate 05 v27 failure record — delayed rejection of unresolved sweeps

## Decision

**Discard v27 and restore v26 as the active baseline.**

The v27 code and its diagnostic ablation remain in the branch as reproducible
negative evidence. They must not be reactivated by changing thresholds or by
selecting only the profitable historical examples.

## Hypothesis tested

The existing detector closed a pool access immediately when same-bar price,
aggressor flow and resting-depth evidence did not form a coherent rejection or
acceptance. v27 tested whether some of those unresolved accesses become valid
reversals through this causal sequence:

```text
material 5m liquidity-pool penetration with existing activity minima
  -> within three completed 1m bars, reclaim the consumed pool
  -> final-15-second aggressor flow turns toward reversal
  -> current aggregate depth supports reversal
  -> unchanged opposite displacement / CHoCH within four bars
  -> unchanged v26 execution, target, costs, slippage and 3% NAV sizing
```

The observer was parallel while the executable new-entry intent and open
position remained globally limited to one.

## Implementation-error check

This was not an execution or accounting failure.

- `smc4 doctor` passed in the fixed image.
- 92 unit and contract tests passed.
- NautilusTrader owned all orders, fills, positions, fees and NAV.
- No order rejection, order denial or liquidation occurred.
- Maximum observed open positions and simultaneous entry intents were both one.

Therefore the negative result is classified as a **scenario-logic error**.

## v27 authoritative three-week evidence

Workflow run `31107715338`, commit
`87ffcbc1348dc8655f0c589bef9b683d59a36f5e`.

| Frozen BTC week | Total return | Geometric daily growth | Trades / wins | Maximum drawdown |
|---|---:|---:|---:|---:|
| 2023-07-09..15 | -4.4072% | -0.6418% | 19 / 9 | 8.5621% |
| 2024-01-15..21 | -3.2974% | -0.4779% | 20 / 11 | 9.8583% |
| 2023-09-08..14 | -7.4738% | -1.1036% | 15 / 8 | 10.1022% |

The added branch increased opportunity count, but losses were not isolated to
one execution path. In Week 3 the delayed-rejection scenarios lost across all
three inherited paths:

| Execution path | Trades / wins | Net PnL |
|---|---:|---:|
| Sponsored CHoCH | 8 / 3 | -7,157.64 USDT |
| Confirmed retrace | 2 / 1 | -1,828.00 USDT |
| Confirmed second touch | 2 / 1 | -1,396.75 USDT |

Thus the defect was not merely marketable-entry timing. The new scenario family
itself mixed genuine liquidation reversals with ordinary countertrend pullbacks
inside continuing auctions. Small targets and occasional wins did not offset
structural-stop losses.

## Required one-variable ablation

The controlled ablation removed only the delayed reclaim / tail-flow / current-
depth stage. Material access, structural invalidation, the unchanged four-bar
CHoCH predicate, execution, target, costs, slippage and 3% current-NAV sizing
were preserved.

Workflow run `31108714671`, commit
`8ddc20a396e912883d28844e5facf92cf8505ef8`, same frozen Week 3:

| Metric | Result |
|---|---:|
| Total return | -23.5851% |
| Geometric daily growth | -3.7699% |
| Trades / wins | 23 / 8 |
| Win rate | 34.7826% |
| Profit factor | 0.1814 |
| Maximum drawdown | 24.2676% |
| Liquidations / rejected orders | 0 / 0 |

Removing the stage made the result substantially worse. Therefore the delayed
response stage had real discriminatory value, but it was insufficient to make
unresolved accesses a positive-expectancy trading scenario. This rules out the
binary conclusions that either the response stage was useless or that retaining
it validated v27.

## Useful components retained as research knowledge

- Parallel observation can coexist with the one-intent/one-position execution
  invariant without implementation failure.
- A later completed response is more informative than promoting every material
  access directly to CHoCH; the ablation quantified that distinction.
- The existing 5m pool universe is too heterogeneous for unresolved-access
  promotion. Every traded Week-3 delayed scenario originated from a single
  `CONFIRMED_5M_SWING` of strength one, not a repeated-liquidity cluster.
- Open-interest sign, response flow magnitude and one-minute depth snapshots did
  not yield a stable causal separator across winners and losers. They must not be
  converted into fitted score thresholds from these few trades.

## Failure cause and largest performance driver

The largest driver was activation of a new reversal family from **internal,
single-touch five-minute swings**. A short-horizon reclaim plus CHoCH identifies
local rotation, but not whether the accessed liquidity was an external objective
whose raid completed the auction. Because the premise was wrong, all inherited
entry paths inherited negative expectancy.

A future candidate must begin from a structurally different event definition,
such as completed higher-order/session external liquidity, rather than repairing
v27 by adding more post hoc filters. Until that candidate is independently
implemented and validated, v26 is the active baseline.
