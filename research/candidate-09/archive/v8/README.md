# Candidate 09 v8 — discarded complete candidate

Reproducible implementation-clean NautilusTrader run: GitHub Actions `31114766445`.
Result commit: `0c810cd3f184632930b405d5de3b7e0ae6b1ec8f`.

## Hypothesis

V8 transferred v4's accepted-breakout-failure mechanism to completed UTC activity
sessions. A reversal required directional approach into the previous session
extreme, two closes outside with displacement/volume/order flow, then loss of the
accepted boundary with opposite micro-structure shift. Baseline entered at the
failure close, kept invalidation beyond the full accepted excursion, and targeted
the opposite edge of the completed source session.

## Frozen-week result

- baseline pooled daily geometric growth: **-0.623391%**
- pooled NAV multiple: **0.876936x**
- baseline trades: **8**, wins: **1**, losses: **7**
- week returns: **-5.9085%, -0.1304%, -6.6780%**
- maximum sampled-segment drawdown: **8.7317%**

Ablations:

- `no-acceptance`: 19 trades, **0.839688x**, daily geo **-0.828569%**
- `failure-retest`: 3 trades, **0.940011x**, daily geo **-0.294157%**
- `midpoint-target`: 1 trade, **0.970002x**, daily geo **-0.144927%**

Removing acceptance increased opportunity but worsened pooled loss and drawdown,
so outside acceptance was a useful noise filter. Waiting for a failed-level retest
and shortening the objective both reduced exposure but neither produced a single
winning ablation path.

## State-path diagnosis

Baseline diagnostics contained:

- 35 directional session-range breaches
- 14 outside acceptances
- 11 accepted-breakout failures
- 8 approved entries
- 3 final cost/geometry rejections

Six of the eight executed reversal trades subsequently crossed the stop beyond the
accepted excursion. One week-b trade timed out near flat; only one week-c US
failure SELL reached the opposite Europe-session edge. The accepted auction's
first loss and opposite MSS therefore behaved mostly as a pullback inside an
otherwise persistent expansion, not as durable reversal evidence.

## Classification

**LOGIC_ERROR_NO_STRUCTURAL_PATH for v8 as a complete candidate.**

Every single-variable ablation remained below 1.0x pooled NAV. Acceptance was
helpful but insufficient; neither failure retest nor target reduction provided a
positive structural path. V8 is not tuned further.

## Valid parts retained

- completed session dealing ranges and causal previous-session liquidity levels
- explicit separation of breach, outside acceptance, failure, and execution
- acceptance confirmation materially reduced low-quality signals
- full event-level rejection diagnostics
- NAV-based full-cost 3% risk sizing and exact Nautilus reconciliation

V9 keeps the accepted session breakout but tests the opposite resolution. After an
apparent failure and opposite MSS, it waits for the original breakout boundary to
be reaccepted with renewed displacement and flow. This is a failed-failure / trapped
countertrend continuation, not v3's first-retest continuation. Invalidation is
inside the failed pullback and the objective is a source-range measured expansion.
