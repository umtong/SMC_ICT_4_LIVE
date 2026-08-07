# Candidate 12 research iterations

All performance figures in this file marked **authoritative** were produced by NautilusTrader account reports using completed one-minute Binance USD-M bars, effective maker/taker costs, adaptive bar ordering, and the candidate's 3% full-NAV loss budget.  Pure state-machine diagnostics are never promoted to performance evidence.

## I0 — initial CLAR state machine

**Code:** `8ede4dcd1d7e85d29bd7a293db96cb44554059fb`  
**Frozen sample:** BTCUSDT W1, 2023-06-25 through 2023-07-02 UTC, with four warm-up days  
**GitHub Actions:** run `31174690104`, job `92853982277`

### Authoritative result

| Measure | Result |
|---|---:|
| Starting NAV | 100,000.00000 USDT |
| Ending NAV | 94,092.19573 USDT |
| Net return | -5.90780427% |
| Daily geometric growth | -0.86528980% |
| Closed trades | 2 |
| Winners / losers | 0 / 2 |
| Win rate | 0% |
| Maximum closed-trade drawdown | 5.90780427% |
| Liquidation | No |
| Risk-budget breach | No |
| Event chronology | Valid |

This iteration failed the project target and the diagnostic-promise gate.  It is retained as negative evidence, not as a success claim.

### Failure decomposition

The engine classified 926 scenarios, but emitted only two executable plans.  The dominant terminal reasons were `COSTED_PLAN_REJECTED` (477), confirmation expiry (432), and `STOP_TOO_WIDE` (269).  Both submitted plans stopped out.

Inspection of the event ledger exposed a state-lifetime defect: a liquidity pool could remain live after price had already crossed it when the corresponding probe was not selected, expired, or failed costed-plan construction.  The same historical level could consequently seed a later scenario even though its first-touch information had already been consumed.  This is an implementation/causality error, distinct from weak trading logic.

## I1 — first-touch liquidity lifecycle

**Code:** `c0b59f63dd1b16fac079d38e2435261a72c524fe`

Changes are deliberately confined to causal state lifetime; economic thresholds and frozen dates are unchanged.

- Every live pool crossed by a completed bar is claimed on that first access, including pools not selected as the active probe.
- A claimed pool cannot merge with a later pivot, become a source again, or act as a future target.
- Low-activity, violent, expired, rejected, and cost-infeasible accesses terminate the source pool rather than silently recycling it.
- Regression tests enforce non-reuse both after a direct access and while another scenario already occupies the state machine.

The next action is a parallel NautilusTrader replay of frozen BTC W1–W3.  Results will determine whether the remaining bottleneck is scenario logic rather than pool lifetime.  W4–W6 remain untouched.
