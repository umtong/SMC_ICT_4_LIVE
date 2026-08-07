# Candidate-09 v17 — Persistent Internal Reacceptance

## Why this experiment exists

The frozen v14 three-year BTC run produced 697 trades over all 36 months but lost
99.9936% after the frozen composite execution cost. Of 554 protective-stop exits,
243 positions closed within one to three minutes and only two of those fast trades won.
The opportunity count was therefore not the limiting factor. The dominant failure was
that the state engine established outside acceptance with consecutive completed closes,
then declared that accepted auction failed after a single completed close back inside and
entered immediately.

v17 tests one causal change: **failure confirmation symmetry**.

## Frozen market-state sequence

1. A completed 15-minute, 60-minute, or daily auction extreme is observed.
2. Directional approach, breach, two outside closes, displacement, volume, excursion,
   and order-flow confirmation establish `ACCEPTED` exactly as in v14.
3. The exact v14 opposite-displacement/flow close inside the failed boundary does not
   enter. It creates `FAILURE_ACCEPTANCE_PENDING`.
4. The immediately following completed one-minute bar must remain inside the same failed
   boundary buffer.
5. A persistent second close permits the reversal at that second completed close.
6. Invalidation is beyond the failed boundary and both confirmation bars. The target is
   the unchanged source-auction equilibrium. Full modeled costs remain inside both the
   reward-to-risk gate and 3% NAV sizing.
7. If the second close does not persist, the prior `ACCEPTED` or `RETESTED` state is
   restored. That same completed bar is then processed as a possible defended retest or
   re-expansion so the control does not suppress a valid state transition.

## Exact controls

| Variant | Changed layer |
|---|---|
| `baseline` | Two-close persistence for all accepted-breakout failures |
| `single-close` | Exact v14 timing and stop logic |
| `after-retest-only` | Persistence only after a defended retest |
| `direct-only` | Persistence only for direct failures from `ACCEPTED` |

Every configuration field other than `failure_confirmation_mode` is assertion-checked as
identical across the four variants. `config_v17.json` is byte-semantically identical to
v14 except for the candidate label.

## What is deliberately unchanged

- fixed BTC weeks and frozen 2022-01-01 through 2025-01-01 long interval;
- detector, auction horizons, approach, breach, acceptance, displacement, and flow logic;
- equilibrium target;
- minimum after-cost reward-to-risk;
- 7.5 bps composite cost per fill;
- full-account NAV and 3% maximum planned loss budget;
- NautilusTrader execution and accounting;
- no continuation entries and no independent sweep scenario.

## Decision rule

The pooled three-week gate is evaluated first. A negative or inactive week is allowed, but
the complete predeclared 21-day period must meet the frozen pooled growth, total-trade,
active-week, concentration, and implementation checks. Only a passing baseline advances to
the frozen three-year evaluation. A control outperforming baseline is diagnostic evidence;
it does not silently replace the baseline after seeing PnL.

## Source integrity

The complete v17 source, tests, corrected economic-failure classifier, and runner are stored
in the five deterministic `v17_bundle/part*` files. Concatenating the parts must produce a
gzip archive with SHA-256
`c3f80199eb1a82df2d1296a6068eea04a7444cdde108c3b0fe926556b1848588`.
The workflow verifies this digest before extraction, runs the readable sources and tests,
and preserves the extracted files in the exact workflow artifact.
