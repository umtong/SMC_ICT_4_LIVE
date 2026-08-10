# ZaratustraV5 lifecycle forensic v2

This run is behaviour-identical instrumentation, not a strategy modification.

## Account identity

- baseline identical: True
- trades: 214
- wins/losses: 144/70
- PF: 0.6810251307578566
- geometric daily growth: -0.013788726002652574
- total return: -0.3406759958962

## Lifecycle decomposition

| outcome | trades | mean R | activation rate | source invalidation rate | dominant temporal order |
|---|---:|---:|---:|---:|---|
| winner | 144 | 0.17596339410465567 | 0.9930555555555556 | 0.4722222222222222 | activation_without_observed_invalidation |
| partial_loss | 44 | -0.2949621055681772 | 0.045454545454545456 | 0.8409090909090909 | invalidation_without_activation |
| full_stop | 26 | -0.9878337253527738 | 0.0 | 0.8846153846153846 | invalidation_without_activation |

## Predeclared interpretation

The next policy change is justified only when losing trades usually lose the same-side source state before reaching the trailing activation, while winners usually activate first. The minimal next experiment would then preserve entry, stop, target and trailing and add only a thesis-failure exit after causal source invalidation. If winner and loser temporal ordering overlaps materially, fixed threshold or time-exit tuning is not justified.

- temporal separation supported: False
- reason: winner activation-first rate=0.528; full-stop invalidation-first rate=0.885
