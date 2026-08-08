# Candidate 18 pre-registration

## Development evidence

Candidate 17's already-observed 2023-12-25 through 2023-12-31 BTC week is development data. It is used only to verify that Candidate 18 changes the identified failure mechanisms:

1. early/middle reversal initiatives are rejected unless they are a first-bar above-baseline notional shock;
2. full-window initiatives remain eligible;
3. remembered defense without depletion proof closes unresolved;
4. next-bar market entry is replaced by a completed-signal IOC LIMIT bracket whose price is the worst permissible fill and whose sizing includes all configured costs.

Development execution experiments are allowed on this already-observed week. The native STOP_LIMIT and BID/ASK-emulation attempts are retained as failed mechanisms. No strategy gate is changed from Candidate 17.

## Untouched evaluation

Before executing Candidate 18, the seed

`candidate18-latency-safe-initiative|3efdf932d37bb997cff95404fb40ee7026a58325|independent-week-1`

is hashed over every Monday from 2022-01-03 through 2025-12-29. It selects index 131: **2024-07-08**. The untouched evaluation is 2024-07-08 through 2024-07-14, with build data beginning 2024-07-05.

The final effective adapter is `candidate18_strategy.py -> latency_capped_ioc_strategy.py`. The strategy, configuration and gate must not be changed after viewing that evaluation. A failure is retained as a failure and attributed from causal events, orders, positions and diagnostics.
