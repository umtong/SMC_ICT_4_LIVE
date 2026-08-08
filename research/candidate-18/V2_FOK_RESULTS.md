# Candidate 18 v2 FOK development result

Candidate 18 v2 replaced the unsafe IOC partial-fill path with a native FOK
LIMIT bracket. It succeeded as an execution invariant: every accepted parent
filled completely or canceled, all planned worst-fill risks stayed below 3%,
maximum realized losses stayed below 2.67%, and no rejection, liquidation,
future reference, duplicate position or naked partial fill occurred.

The execution safety came at a material opportunity cost:

| Development period | Return | Daily geometric growth | Trades | W/L | PF | MDD | Gate |
|---|---:|---:|---:|---:|---:|---:|---|
| 2023-12-25..31 | +15.1301% | +2.0331% | 6 | 5/1 | 10.08 | 3.57% | fail: 6 < 7 trades |
| 2024-07-08..14 | -2.0467% | -0.2950% | 7 | 2/5 | 0.78 | 7.81% | fail |
| 2023-10-16..22 | -2.8302% | -0.4093% | 7 | 2/5 | 0.66 | 5.79% | fail |

All three periods are development data. The viewed-week failures show that FOK
cannot be promoted merely because it is safe. Candidate 18 v3 instead uses the
same IOC price cap with NautilusTrader's explicit proportional OTO trigger so
every partial fill receives matching protective children while retaining the
partially executable opportunity set.
