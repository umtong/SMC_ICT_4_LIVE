# 4h jump arbitration × boundary-handoff result

This is a causal mechanism experiment, not a pass/fail gate.  The interval is
now development data.  Read each cell's `trade_rows.json`, scenario events and
orders before modifying the selector or handoff.

| cell | trades | W/L | PF | total return | geo/day | MDD |
|---|---:|---:|---:|---:|---:|---:|
| source_max_z__no_handoff | 9 | 3/6 | 0.332 | -7.091% | -0.524% | 9.739% |
| least_qualifying_z__no_handoff | 9 | 3/6 | 0.586 | -4.039% | -0.294% | 8.553% |
| source_max_z__deferred_handoff | 9 | 3/6 | 0.332 | -7.091% | -0.524% | 9.739% |
| least_qualifying_z__deferred_handoff | 9 | 3/6 | 0.586 | -4.039% | -0.294% | 8.553% |

A raw symbol candidate count is not an independent opportunity count.  Same
4-hour boundary candidates must remain grouped, and handoff value must be read
from the continuous one-slot account path.
