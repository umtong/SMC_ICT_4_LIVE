# Candidate 12 research iterations

All performance figures in this file marked **authoritative** were produced by NautilusTrader account reports using completed one-minute Binance USD-M bars, effective maker/taker costs, adaptive bar ordering, and the candidate's 3% full-NAV loss budget. Pure state-machine diagnostics are never promoted to performance evidence.

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

This iteration failed the project target and the diagnostic-promise gate. It is retained as negative evidence, not as a success claim.

### Failure decomposition

The engine classified 926 scenarios, but emitted only two executable plans. The dominant terminal reasons were `COSTED_PLAN_REJECTED` (477), confirmation expiry (432), and `STOP_TOO_WIDE` (269). Both submitted plans stopped out.

Inspection of the event ledger exposed a state-lifetime defect: a liquidity pool could remain live after price had already crossed it when the corresponding probe was not selected, expired, or failed costed-plan construction. The same historical level could consequently seed a later scenario even though its first-touch information had already been consumed. This is an implementation/causality error, distinct from weak trading logic.

## I1 — first-touch liquidity lifecycle

**Code:** `c0b59f63dd1b16fac079d38e2435261a72c524fe`  
**GitHub Actions:** run `31176348389`, jobs `92859150890`, `92859150900`, `92859150906`

Changes were deliberately confined to causal state lifetime; economic thresholds and frozen dates were unchanged.

- Every live pool crossed by a completed bar is claimed on that first access, including pools not selected as the active probe.
- A claimed pool cannot merge with a later pivot, become a source again, or act as a future target.
- Low-activity, violent, expired, rejected, and cost-infeasible accesses terminate the source pool rather than silently recycling it.
- Regression tests enforce non-reuse both after a direct access and while another scenario already occupies the state machine.

### Authoritative BTC result

| Week | Ending NAV | Net return | Daily geometric growth | Trades | Wins | Losses |
|---|---:|---:|---:|---:|---:|---:|
| W1 | 94,092.08939 | -5.90791061% | -0.86530595% | 2 | 0 | 2 |
| W2 | 97,452.21189 | -2.54778811% | -0.36796599% | 1 | 0 | 1 |
| W3 | 87,620.61100 | -12.37938900% | -1.87251787% | 6 | 1 | 5 |

I1 fixed the causality defect but failed economically. Running W2/W3 after the decisive W1 failure was itself an inefficient validation decision; those results are retained only as historical negative evidence and are not the protocol going forward.

All nine submitted trades were acceptance scenarios sourced from isolated `CONFIRMED_15M_PIVOT` levels. A local internal pivot was therefore being promoted to external liquidity without higher-timeframe evidence. This is a scenario-logic error rather than a fill-engine or accounting failure.

## I2 — external acceptance auction diagnostic

**Staged by:** `226cdab5098f9796a7d1cb916244fa62ac33bb66`

I2 changed the scenario ontology rather than tuning the losing thresholds.

- Executable source liquidity was restricted to a causally available prior four-hour boundary or prior UTC-day boundary.
- Rejection execution was prohibited because one-minute taker-buy volume cannot demonstrate passive quote replenishment.
- Acceptance required excursion, delayed retest, continued acceptance, reacceleration, friction-dominating structural stop distance, and a live structural target.

A non-authoritative W1 state replay emitted only one plan. That is not a structural path to the required day-trading opportunity rate, so I2 did not advance to Nautilus W2/W3 evaluation.

## I3 — W1-only completed-session auction replacement

The research protocol is corrected: W1 alone separates implementation faults from economic-logic failure. No confirmation week or long evaluation runs until W1 simultaneously meets frequency, post-cost growth, payoff, and win-rate requirements.

The prior candidate was replaced by two explicit causal scenarios built from completed Asia and London ranges:

1. failed auction: boundary sweep, close back inside, MSS displacement, pullback, reacceleration;
2. accepted auction: sustained closes outside, retest of the accepted boundary or displacement mean threshold, reacceleration.

Targets are pre-existing session/prior-day liquidity or a measured completed-session expansion. Stops remain beyond the causal pullback or sweep, and all plans must pass full costed structural R before Nautilus submission.

A local causal W1 opportunity replay emitted nine plans before order-slot and portfolio interaction. This justifies exactly one authoritative W1 NautilusTrader run; it is not performance evidence.
