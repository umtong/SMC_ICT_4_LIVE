# Candidate-06 SIPR terminal report

## Execution integrity

The SIPR state machine, factor-isolation tests, inherited causality tests and the
NautilusTrader 1.230.0 campaign completed without implementation or runtime
errors.  Fees, one-tick slippage, probabilistic limit touches, delayed entries,
position events, current-NAV risk sizing and the single portfolio slot remained
unchanged.  Therefore the first-week outcome is interpreted as a logic result.

## Controlled first-week results

| Variant | Geometric NAV/day | Net PnL after cost | Trades | Wins | PF | MDD |
|---|---:|---:|---:|---:|---:|---:|
| `sipr_full` | 0.0000% | 0.00 USDT | 0 | 0 | undefined | 0.00% |
| `sipr_sequence_only_ablation` | 0.0000% | 0.00 USDT | 0 | 0 | undefined | 0.00% |
| `sipr_impact_only_ablation` | -1.2887% | -8,680.10 USDT | 3 | 0 | 0.000 | 8.68% |
| `sipr_raw_15m_reference` | -1.7448% | -11,591.57 USDT | 4 | 0 | 0.000 | 11.59% |

The full engine observed 33 first accepted auctions, but only one immediately
following auction independently persisted in the same direction with effective
impact.  That context did not produce an eligible downstream fill.  The
sequence-only ablation confirmed five sequences and armed two downstream
signals, but both were consumed before a valid fill by favorable-move and
net-reward-risk checks.  Removing sequencing exposed three impact-only trades;
removing both factors exposed four raw 15-minute trades.  Every filled trade
stopped.

## Logic diagnosis

The experiment separates two findings that must not be collapsed into a binary
pass/fail statement.

1. **Sequential acceptance is a real precision mechanism.** It sharply reduced
   the number of provisional contexts and prevented the losing raw/impact-only
   fills.  A single accepted auction was frequently not followed by independent
   continuation.
2. **Sequential acceptance is not an alpha source in the inherited entry
   scenario.** Even the confirmed contexts did not generate a cost-viable first
   pullback through the existing counter-bias sweep and one-minute response
   chain.  The sequence-only variant's two armed signals were already consumed
   or had eroded net reward-risk before entry.
3. **Impact efficiency remains only a classifier.** Without sequence it selected
   three trades, all losses.  With sequence it made the state almost inactive.
4. **The common failure is downstream of context selection.** Across HML, AFHR,
   SIAR, ACSR and SIPR, increasingly strict higher-timeframe state definitions
   reduce false opportunity but do not repair the inherited assumption that a
   counter-context liquidity sweep followed by a short response is the correct
   continuation entry.

## Decision and retained learning

SIPR is rejected as a complete candidate.  No sealed-week or long evaluation is
authorized, and its impact/sequence thresholds must not be polished.

Retain:

- immediately consecutive completed-auction evidence as a causal precision
  primitive;
- suspension of stale context while persistence is unresolved;
- strict reset on a nonpersistent next auction;
- the empirical conclusion that direction filters are no longer the primary
  research bottleneck.

The next independent candidate must replace the entry scenario itself.  It must
model displacement, imbalance formation, first mitigation and renewed acceptance
as separate completed states rather than inherit the same sweep-reclaim trigger.
