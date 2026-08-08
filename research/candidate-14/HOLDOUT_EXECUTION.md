# Candidate 14 frozen holdout execution

The metadata trigger at `f2d116e1b9e9672b9d54ea944bfb6809be1590f8` did not execute the reserved H01-H16 intervals. The active workflow still contained the W10-W14 diagnostic matrix, so commit `450ad7fba5090b27cca0bf35151dfd7c80958171` is a repeated diagnostic evidence commit, not frozen holdout evidence.

This is an orchestration defect, not a strategy or market-logic failure.

The corrected workflow:

- keeps every strategy, semantic, execution, cost, stop, target, risk and arbitration blob byte-identical to the pre-outcome reservation;
- verifies each current Git blob against the strategy source commit recorded in `HOLDOUT_RESERVATION.json`;
- runs the deterministic H01-H16 quarterly intervals with NautilusTrader;
- writes evidence only under `research/candidate-14/holdout/`;
- retains the original W10-W14 diagnostics separately.

No H01-H16 outcome was used to modify the strategy. Any later strategy change invalidates this holdout set.
