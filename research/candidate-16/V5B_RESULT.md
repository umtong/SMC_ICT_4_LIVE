# Candidate 16 v5b result

## Decision

`DO NOT CLAIM TARGET; ABANDON CROWDED-INITIATIVE FADE`

The v5 STOP_LIMIT trigger race was repaired with the repository-proven FOK
all-or-none price cap while preserving the state, direction, stop, target,
costs, 3% worst-fill risk, gate, and pre-registered dates.

## Same pre-registered evaluation

- evaluation: 2023-06-05 through 2023-06-11
- L1 join coverage: 100%
- strategy-ready feature rows: 14,243
- cost-exceeding observations: 105
- qualified crowded shocks: 17
- later failures confirmed: 13
- FOK entry submissions: 12
- closed positions: 8
- wins / losses: 1 / 7
- win rate: 12.5%
- ending NAV: 90,252.63659359 USDT
- total return: -9.74736340641%
- daily geometric growth: -1.45442491030%
- max drawdown: 10.6975425959%
- profit factor: 0.06030698049

Four rejection diagnostics remained, including two protective fail-close paths,
but they do not rescue the economic hypothesis: seven of eight actual positions
lost and most failed within minutes after entry.

## Structural conclusion

A cost-exceeding impulse with new OI and opposite closing L1 pressure did not
produce a reliable tradeable fade on the independent week. The earlier
development-week terminal reversal was not stable. Threshold tuning is not
justified.

The reusable findings are:

1. explicit Parquet nanosecond normalization and 95% fail-closed L1 coverage;
2. state and later confirmation must remain separate;
3. FOK price-capped entry prevents late partial or trigger-race fills;
4. the complementary state -- new OI with L1 pressure aligned to the impulse --
   showed persistent directional continuation in both development and the first
   independent period and therefore owns the next experiment.

- workflow run: `31254465028`
- artifact: `candidate-16-v5b-screen-f7207386d09b90a4d3a6e23eb42a67252572948b`
