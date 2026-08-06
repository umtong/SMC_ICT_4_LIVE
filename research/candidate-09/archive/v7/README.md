# Candidate 09 v7 — discarded complete candidate

Reproducible implementation-clean NautilusTrader run: GitHub Actions `31113724429`.
Result commit: `ea450a23602e9e942fc786f204de71b87259de56`.

## Hypothesis

V7 used the immediately previous UTC activity-session range as the dealing range.
A reversal required a sweep/reclaim of one edge, opposite displacement and micro
structure shift leaving a three-candle fair-value gap, and a first causal FVG
mitigation/rejection. Entry was near the swept edge, stop beyond the observed
sweep, and the default target was the opposite edge of the completed source
session.

## Frozen-week result

- baseline pooled daily geometric growth: **-0.289679%**
- pooled NAV multiple: **0.940898x**
- baseline trades: **2**, wins: **0**, losses: **2**
- week returns: **0.0000%, -5.9102%, 0.0000%**
- maximum sampled-segment drawdown: **5.9102%**

Ablations:

- `no-flow`: 2 trades, **0.940894x**
- `midpoint-target`: 1 trade, **0.969999x**
- `no-fvg-retest`: 3 trades, **0.991823x**

`no-fvg-retest` added one successful week-c SELL (+5.4123% week return), but the
two week-b reversals still stopped. Therefore removing the retest improved timing
and opportunity but did not produce positive pooled expectancy or a transferable
complete candidate.

## State-path diagnosis

Baseline diagnostics contained:

- 28 previous-session liquidity sweeps/reclaims
- 7 opposite displacement/MSS/FVG confirmations
- 5 FVG mitigation/rejections
- 2 approved entries
- 2 stopped trades

The two baseline trades had strong ex-ante net reward-to-risk estimates
(approximately 3.97 and 2.58), so v7 fixed v6's untradeable geometry. The failure
was directional: a wick sweep/reclaim plus opposite displacement did not prove
that the external auction had actually failed. FVG mitigation improved entry
location but did not distinguish a temporary pullback from a durable reversal.

## Classification

**LOGIC_ERROR_NO_STRUCTURAL_PATH for v7 as a complete candidate.**

The best single-variable ablation remained below 1.0x pooled NAV. V7 is therefore
not tuned or extended with more thresholds.

## Valid parts retained

- completed Asia/Europe/US/Late session dealing ranges
- explicit separation of session-level detection from trade-scenario state
- entry/stop/target geometry capable of surviving composite execution cost
- diagnostic separation of sweep, displacement, FVG mitigation, signal rejection,
  and final execution
- NAV-based full-cost 3% risk sizing with Nautilus reconciliation

V8 retains the session dealing range and viable opposite-edge objective, but does
not treat a wick sweep as a failed auction. It requires consecutive closes and
displacement outside the range, followed by loss of that accepted level with an
opposite micro-structure shift. This transfers the accepted-breakout-failure
mechanism that was valid in v4 into the session context.
