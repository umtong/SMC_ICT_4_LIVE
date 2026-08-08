# Candidate 15 V1 failure evidence

V1 was a valid but rejected weekly-reset screen. It is preserved separately so
V2 cannot overwrite the evidence that motivated the structural change.

## Frozen result

- protocol: `candidate-15-sequential-response-router-v1`
- intervals: D1 `2026-06-08`, H1 `2026-04-06`, S1 `2025-10-10`
- weekly-reset NAV multiple: `0.9227886760522757`
- daily geometric growth: `-0.0038191182630036556`
- closed trades: `5`
- wins / losses: `1 / 4`
- maximum interval closed-trade drawdown: `0.061642905672`
- liquidation: none
- engine errors: none
- classification: `CANDIDATE15_SCREEN_REJECTED`

## Failure decomposition

Four trades came from `SCDAM_CORE`. Their router snapshot contained the latest
sweep timestamp, number of accumulated observations and entry timestamp. With
one-minute bars, the number of completed bars between the router's boundary
crossing and entry is:

| interval | trade | result | minutes latest sweep to entry | observations at resolution | reused decision bars |
|---|---|---:|---:|---:|---:|
| D1 | XRPUSDT FAR short | loss | 25 | 12 | 13 |
| H1 | SOLUSDT FAR short | loss | 33 | 25 | 8 |
| H1 | ETHUSDT FAR long | win | 11 | 10 | 1 |
| S1 | SOLUSDT FAR long | loss | 34 | 7 | 27 |

V1 stopped observing once `ACCEPTANCE` or `FAILURE` was reached. The same label
could therefore be reused much later even though inherited entry confirmation
belonged to a different micro-auction. This is a lifecycle error, not evidence
that a PnL threshold should be tuned.

The fifth trade was a `SESSION_I7` BTC long in D1. It never passed through the
Candidate 15 response router because Candidate 14 adds that module at the shared
portfolio layer. V2 must either implement a compatible continuously observed
session response episode or fail closed. It chooses the latter rather than
pretending the candidate covered a scenario family it did not classify.

## V2 structural correction

1. A resolved response is usable on the resolution bar and the immediately
   following completed bar only.
2. An unused resolution then becomes `STALE`, which is a no-trade state.
3. A new sweep extreme resets the episode.
4. `SESSION_I7` plans are recorded but rejected as
   `C15_UNROUTED_SCENARIO_FAMILY` until a dedicated router exists.
5. D1/H1/S1 are contaminated mechanism replays. V2 classification uses only the
   separately predeclared U1-U5 intervals.
