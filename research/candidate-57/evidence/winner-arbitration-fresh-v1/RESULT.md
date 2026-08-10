# Winner15m fresh one-slot arbitration result

This is a mechanism comparison, not a pass/fail gate.  The interval is now
development data.  Read `comparison.json`, both `closed_scenarios.json` files
and event/order evidence before changing the router.

| policy | completed trades | win rate | PF | total return | MDD |
|---|---:|---:|---:|---:|---:|
| current_max_climax | 30 | 43.33% | 0.610 | -9.52% | 12.99% |
| least_volume_excess | 28 | 39.29% | 0.538 | -10.16% | 14.35% |

The policy difference is interpretable only if both runs are end-flat and the
same signal/management code path was preserved.  Aggregate superiority alone
does not establish a reusable arbitration rule.
