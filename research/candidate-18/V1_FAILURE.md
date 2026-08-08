# Candidate 18 v1 locked failure

The v1 strategy remains frozen at `acd078c352712b3aa0440d00cbc5221f1f388ba3`.
Its IOC LIMIT parent solved stale next-bar market fills on development data, but
untouched execution exposed a more serious contingent-order failure.

On the untouched 2023-10-16 through 2023-10-22 week, the first short parent was
sized for a 2.99995% planned worst-fill loss. The IOC order filled only 14.288
of 36.790 BTC and canceled the remainder. Canceling the OTO parent also removed
its stop and target children. The naked partial short stayed open until the
180-minute forced exit and lost 9.0738% of contemporaneous equity.

This is an implementation failure, not evidence against or for the market-state
hypothesis. v1 results are retained without repair:

- 2024-07-08 through 2024-07-14: +6.6111%, 11 trades, daily geometric growth
  0.9187%, performance gate failed; integrity checker also had an inverted
  event/observation timestamp comparison.
- 2023-10-16 through 2023-10-22: -0.7284%, 13 trades, daily geometric growth
  -0.1044%, performance gate failed; one unprotected partial fill lost 9.0738%.

Candidate 18 v2 changes only execution atomicity: the same capped LIMIT bracket
uses FOK so a parent fills in full or opens no position. The viewed v1 periods
become development data for v2. New untouched periods must be selected only
after the v2 code is locked.
