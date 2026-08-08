# Candidate 05 — opportunity-density feasibility

## Purpose

This is an algebraic feasibility check, not a return objective, leverage cap or
position-size override. The project risk contract remains exactly 3% of current
NAV per planned loss. The check asks whether an observed trade count could have
reached the project growth requirement even under an unrealistically favorable
path in which every trade wins at the strategy's existing maximum post-cost
target.

## Existing contracts

```text
risk fraction per planned loss = 0.03
maximum selected post-cost target = 1.50 R
best possible NAV multiple per trade = 1 + 0.03 * 1.50 = 1.045
```

For `D` calendar days and `N` trades, the all-winner upper-bound daily growth is

```text
best_case_gdg(D, N) = 1.045 ** (N / D) - 1
```

The minimum number of all-winner, maximum-target trades required to reach 1%
calendar-day geometric growth is

```text
ceil(D * log(1.01) / log(1.045))
```

This upper bound ignores fees beyond those already included in post-cost R,
missed targets, time exits, partial favorable movement, and every loss. Real
requirements are therefore strictly higher.

## Exact implications

| Evaluation length | Minimum 1.5R winners with zero losses |
|---:|---:|
| 7 days | 2 |
| 30 days | 7 |
| 91 days | 21 |
| 912 days | 207 |

A 30-day candidate producing five positions has the following absolute ceiling
under the frozen maximum target:

```text
30-day best multiple = 1.045 ** 5 = 1.2461819378
best-case GDG = 1.2461819378 ** (1 / 30) - 1
              ≈ 0.7365% per calendar day
```

Thus a five-trade month cannot reach 1% daily geometric growth under the current
1.50R maximum even at 100% wins. This is a structural density shortfall, not a
request to raise leverage or risk. The logical system must discover more valid,
independent opportunities or a genuinely more distant causal destination.

For a representative 0.75R target, one maximum winner adds 2.25% NAV. Even with
zero losses, roughly fourteen such winners are required over 30 days. With a
70% win rate and one-R losses, expected R per trade is

```text
0.70 * 0.75 - 0.30 * 1.00 = 0.225 R
```

or approximately 0.675% NAV before compounding per trade at the fixed 3% risk.
About forty-five independent trades per 30 calendar days would then be needed
to approach the required log-growth. Correlated trades from one auction episode
cannot be counted as independent confirmations of that rate.

## Research decision

A rare, high-accuracy external-reversal branch may be useful, but it cannot be
the complete project system when its observed opportunity density lies below
the all-winner feasibility bound. Protecting such a branch with more filters
only lowers the ceiling further. It must be paired with a separate, independently
positive and causally distinct frequent auction family; alternatively it must
be discarded as a complete-system candidate.

The feasibility check is applied only after a real NautilusTrader run. It never
changes the order, quantity, stop, target, fee, slippage, liquidation or one-slot
contracts.
