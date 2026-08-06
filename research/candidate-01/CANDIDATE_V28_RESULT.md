# Candidate 01 v28 Result — Rejected

## Frozen hypothesis

`v28` modeled a market-microstructure sequence rather than another candle
filter:

```text
cost-resolved outside aggressive-flow initiative
→ opposite-flow pullback while outside value remains accepted
→ aligned-flow resumption through the completed pullback extreme
→ continuation to one pre-initiative structure-width projection
```

The primary rule additionally required initiative price/flow elasticity above
its causal median and resumption elasticity above adverse pullback elasticity.
The single ablation removed only those elasticity comparisons.

All performance evidence used official Binance Vision BTCUSDT aggregate trades
as one-for-one NautilusTrader `TradeTick` objects. NautilusTrader 1.230.0 owned
orders, fills, fees, margin, positions, PnL and NAV at current-equity 3% planned
risk and 7 bp per side.

## Implementation error and controlled repair

The first implementation extended `pullback_high`/`pullback_low` with the
current resumption event before testing the break. A long therefore required a
completed close above its own high, and a short required a close below its own
low.

This was an implementation error, not a strategy result. The candidate,
parameters and frozen BTC week were left unchanged. The code was repaired to:

1. snapshot the prior completed pullback extreme;
2. test the current completed close against that prior extreme;
3. only then extend the pullback path when no resumption was completed.

The same week was rerun through NautilusTrader.

## Corrected first-week result

Frozen primary week: `2024-05-13` through `2024-05-20` UTC.

### Primary elasticity rule

```text
initiative events                    20
initiatives armed                     6
outside value lost                    4
target consumed before entry          1
response window expired               1
plans                                  0
trades                                 0
```

The high-elasticity state was too restrictive and no complete scenario reached
execution.

### Single ablation: sequence only

```text
initiatives armed                    20
accepted counterflow pullbacks       13
completed price resumptions           4
evaluation plans                      3
Nautilus submissions                  0
Nautilus closed positions             0
```

All three evaluation plans were rejected by the unchanged cost-after geometry
contract. Their approximate net reward/risk values were:

```text
0.159
0.597
0.045
```

## Failure diagnosis

The dominant failure was not the direction sequence. The ablation demonstrated
that the system repeatedly observed:

```text
outside initiative → accepted counterflow pullback → renewed price advance
```

The dominant failure was **entry timing**. Waiting for a later equal-notional
event to complete above/below the pullback extreme consumed most of the target
distance. By the first executable venue trade after that completed event, the
remaining reward could not cover the complete structural stop and 14 bp
round-trip execution cost.

## Valid component retained

The following component survived and is carried into `v29`:

- external structure and cost-resolved initiative are observed before a setup;
- counterflow must produce an actual adverse move;
- the pullback must retain outside value and leave the measured target intact;
- the complete initiative/pullback path defines invalidation;
- a resumption break is the conditional entry event, not a filter applied after
  entry.

## Rejected components

- causal elasticity-above-median requirement: too sparse;
- waiting for a completed resumption event and then entering at market: too
  late for cost-after geometry.

## Decision

`v28` is rejected and was not opened on a second week or long evaluation.
`v29` changes exactly one structural variable: it arms a NautilusTrader
conditional entry at the already completed pullback extreme, so execution can
occur on the first actual resumption trade rather than after a later completed
event.
