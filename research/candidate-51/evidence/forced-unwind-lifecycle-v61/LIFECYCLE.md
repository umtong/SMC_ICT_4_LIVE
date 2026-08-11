# Accepted forced-unwind causal lifecycle v61

- frozen configuration: `{'entry_mode': 'direct', 'stop_mode': 'impulse_origin', 'target_mode': 'two_r', 'hold_min': 480}`
- raw asset episodes: 84
- same-clock candidates after existing arbitration: 47
- market-wide causal episodes: 40
- v59 reproduction: `True`
- conclusion: **lifecycle_and_rejection_repair_supported**

| policy | trades | trades/day | mean R | median R | PF | ex-best R | final diagnostic NAV | daily geom | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v59_fixed_hold_baseline | 39 | 0.279 | 0.382 | 0.119 | 3.33 | 0.342 | 1.540 | 0.309% | -7.64% |
| actual_exit_only | 43 | 0.307 | 0.365 | 0.115 | 3.10 | 0.328 | 1.574 | 0.325% | -10.41% |
| episode_locked_actual_exit | 40 | 0.286 | 0.421 | 0.174 | 3.63 | 0.382 | 1.629 | 0.349% | -7.64% |
| episode_locked_rejected15_exit | 40 | 0.286 | 0.432 | 0.174 | 3.91 | 0.394 | 1.652 | 0.359% | -6.22% |

## Predeclared hypothesis assessment

- actual_exit_recovers_available_slots: `True`
- episode_lock_removes_repeats_without_destroying_edge: `True`
- rejected15_exit_truncates_the_predicted_loss_group: `True`
- persistent15_winners_are_unchanged: `True`

## Truth boundary

This audit corrects lifecycle and episode accounting for one low-frequency specialist. It does not satisfy the final frequency or continuous NautilusTrader NAV requirement.
