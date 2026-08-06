# Candidate 09 v9 — discarded complete candidate

Reproducible implementation-clean NautilusTrader run: GitHub Actions `31115541106`.
Result commit: `350e5d6fed6824eba2958da34387f4e8cdbec641`.

## Hypothesis

V9 treated the common v8 reversal stop-out as information. It required a previous
session breakout to achieve outside acceptance, show an apparent failure with an
opposite micro-structure shift, and then reaccept the original boundary with
renewed original-direction displacement, volume, flow and micro-structure break.
Baseline entered this failed-failure continuation and targeted one completed
source-range width beyond the broken edge.

## Frozen-week result

- baseline pooled daily geometric growth: **-0.434152%**
- pooled NAV multiple: **0.912680x**
- baseline trades: **3**, wins: **0**, losses: **3**
- each frozen week produced exactly one trade and approximately one full planned loss
- maximum sampled-segment drawdown: **3.0001%**

Ablations:

- `plain-acceptance`: 4 trades, **0.885295x**, daily geo **-0.578486%**
- `reacceptance-retest`: 0 trades, **1.000000x**
- `half-range-target`: 1 trade, **0.969999x**, daily geo **-0.144942%**

## State-path diagnosis

Baseline diagnostics contained:

- 35 directional session-range breaches
- 14 outside acceptances
- 11 apparent accepted-breakout failures
- 4 failed-failure reacceptances
- 3 approved continuation entries
- 3 stop losses

The second-order trap sequence was observable and produced one candidate in every
frozen week, but all three reaccepted breakouts failed again. Entering the first
outside acceptance was worse, waiting for a retest eliminated every trade, and a
shorter measured objective did not change the losing direction.

## Classification

**LOGIC_ERROR_NO_STRUCTURAL_PATH for v9 as a complete candidate.**

Every executable single-variable variant lost. The fixed previous-session boundary
has now failed as a universal reversal source (v7/v8) and as a continuation source
(v9). This hypothesis family is retired rather than tuned.

## Valid parts retained

- outside acceptance filters low-quality breaches
- explicit failed-failure/reacceptance state is mechanically diagnosable
- three independent frozen weeks prevented a one-week inference
- full-cost 3% loss sizing and Nautilus accounting remained exact

V10 returns to the only positive complete run in the branch, v4. It performs the
missing exact controlled decomposition: original v4 trapped-breakout reversal
logic and equilibrium target are preserved, while the two repeatedly losing
components—continuation entries and 240-minute source levels—are disabled in the
baseline. Ablations restore each component separately and remove flow separately.
