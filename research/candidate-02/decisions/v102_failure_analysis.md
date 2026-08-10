# Candidate-02 v102 failure analysis

## Verdict

`v102 quarter-hour impact retention` is discarded before a performance run because its **causal signal ceiling** makes the weekly trade-frequency gate mathematically impossible in the adverse revealed week.

This is not a PnL estimate and not a replacement backtest. NautilusTrader remains the only permitted performance engine. The verdict is based on a stricter pre-execution condition: completed trades can never exceed scheduled causal signals.

## Intended causal chain

```text
quarter-hour event clock
→ extreme opening ten-second flow, turnover and return
→ same-direction full-minute flow and price impact
→ no material front-side depth refill
→ price impact remains after 2–4 completed minutes
→ response flow has not changed sign
→ enter retained-impact direction
→ invalidate through the pre-burst close
→ target one frozen prior 60-minute accepted-close range
```

The design directly addressed v77's main error: an opening burst is not necessarily persistent price discovery.

## Causal opportunity ceiling

The locked signal generator produced the following final signal counts before any order or fill logic:

| Revealed week | 2-minute | 3-minute central | 4-minute | 3-minute no-response-flow ablation |
|---|---:|---:|---:|---:|
| 2024-09-16 | 6 | 5 | 3 | 6 |
| 2024-01-29 | 4 | 2 | 6 | 3 |

The weekly gate is at least `0.75 completed trades/day`. Over seven days, this requires at least six completed trades. The central 3-minute state schedules only two signals in the adverse week. The one permitted ablation schedules three. No execution model can transform two or three causal signals into six completed trades.

## Implementation versus logic

### Implementation status

- Source, configuration and lock validate and compile.
- The direct aggTrade/bookDepth feature matrices are the already revealed v77 development inputs.
- No future label is used.
- The Actions queue did not provide a fresh NautilusTrader performance result during this iteration.
- No performance statistic is inferred from the diagnostic path.

The queue blockage is an execution-infrastructure issue, but it does not prevent the frequency impossibility proof.

### Logic failure

The bottleneck is the conjunction of:

1. fixed quarter-hour timing,
2. full opening shock qualification,
3. low depth refill,
4. complete impact retention,
5. response-flow confirmation.

That conjunction is too rare outside the originally favorable week.

## Single-variable ablation

Removed variable:

```text
minimum_response_flow_alignment
```

Everything else was unchanged. The adverse week increased from two to only three final signals. Therefore response-flow sign is not the dominant bottleneck, and removing it does not create a viable day-trading opportunity set.

## Useful components retained

- Measuring **impact retention** is more causal than treating aggressive-flow magnitude as continuation by itself.
- Response-flow sign is a quality variable, but not the primary cause of scarcity.
- Signal-frequency upper bounds should be checked before expensive execution runs.
- Quarter-hour timing may be useful as context, but it should not be the only event generator.

## Requirement for the next candidate

The next design must replace the fixed clock with an endogenous information clock:

```text
completed turnover/order-flow accumulation
→ metaorder initiation
→ continued participation or completion
→ retained-impact continuation OR decayed-impact reversion
```

Continuation and reversion must be mutually exclusive, with separate natural targets and standalone diagnostics. No new random BTC week is opened for v102.
