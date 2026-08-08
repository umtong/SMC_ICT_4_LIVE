# Candidate 18 locked independent-validation plan

## Immutable system

The effective system is frozen at commit
`acd078c352712b3aa0440d00cbc5221f1f388ba3`.

The following paths are immutable for every evaluation below:

- `research/candidate-18/*.py`
- `research/candidate-18/config.json`
- inherited Candidate 17, Candidate 16 and Candidate 05 execution/runner code

Only validation workflows and result summaries may be added after the lock.
No strategy, threshold, risk, cost, gate or order-policy change is permitted
after any untouched result is opened.

## Development data

BTCUSDT 2023-12-25 through 2023-12-31 was already observed by Candidate 17 and
is development data. Candidate 18 used it only to replace identified causal and
execution failures. Its positive result is not counted as independent evidence.

## Deterministic untouched periods

Each seed is hashed with SHA-256. The integer digest is reduced modulo the 209
Mondays from 2022-01-03 through 2025-12-29. The selected Monday is the
evaluation start. All four selections were committed before any of their data
or results were opened under the locked system.

| ID | Seed suffix | Index | Evaluation |
|---|---:|---:|---|
| independent-week-1 | Candidate 17 snapshot seed retained from original pre-registration | 131 | 2024-07-08 through 2024-07-14 |
| independent-week-2 | `candidate18-final\|<LOCK>\|independent-week-2` | 93 | 2023-10-16 through 2023-10-22 |
| independent-28d-1 | `candidate18-final\|<LOCK>\|independent-28d-1` | 46 | 2022-11-21 through 2022-12-18 |
| independent-91d-1 | `candidate18-final\|<LOCK>\|independent-91d-1` | 14 | 2022-04-11 through 2022-07-10 |

`<LOCK>` is the immutable commit SHA above. Each build starts three calendar
days before evaluation for causal warm-up. Evaluation NAV always begins once at
100,000 USDT and remains one continuous NautilusTrader account for the complete
period.

## Integrity requirements

Every run must retain:

- NautilusTrader BacktestNode order, fill, fee, position, margin and NAV ownership;
- configured 7.5 bps fee plus 2.5 bps adverse slippage per side;
- funding and inherited forced-flat rules;
- no future observation timestamps;
- no liquidation or non-benign order rejection;
- at most one global entry intent or position;
- positive continuous equity;
- IOC LIMIT entry parents tagged `CANDIDATE18_IOC_PRICE_CAP`;
- planned loss at the worst permissible entry fill no greater than 3% of
  contemporaneous NAV.

An integrity failure invalidates the run. A valid run can still fail the
performance gate and must be retained as a strategy failure.

## Performance decision

The unchanged gate is evaluated from cost-after continuous NAV. Its minimum
daily geometric-growth threshold is 1%, together with the existing trade,
win-rate, activity, drawdown, concentration and account-integrity checks.
Results are reported per period and jointly; no parameter or route is changed
between periods.
