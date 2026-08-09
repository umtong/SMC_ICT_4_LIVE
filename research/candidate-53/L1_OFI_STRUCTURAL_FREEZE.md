# Candidate 53 — Structural true-L1 OFI continuation freeze

Frozen before any 2024-01-22..2024-01-28 data is opened.

This policy is a complete causal scenario derived from the already frozen q90
true-L1 OFI mechanism plus external state-first microstructure evidence.  It is
not selected from the later-window outcomes.

## Policy

1. Build the same causal approximately-30/day dollar-volume participation bars
   and true Cont-style top-of-book OFI as `L1_OFI_FREEZE.md`.
2. Candidate event requires absolute OFI >= its own trailing 90th percentile.
3. Direction is the OFI sign.
4. **Price acceptance state:** the completed participation bar itself must have
   a strictly positive return in the OFI direction.  If aggressive pressure did
   not produce same-direction price progress, classify it as ABSORBED / NO TRADE.
5. Entry is strictly the next one-minute open after the participation bar.
6. Structural invalidation is the participation bar's first-minute open.  The
   trade is valid only when that level is on the adverse side of the entry.
   Returning through the full origin of the pressure leg invalidates the
   continuation auction.
7. Planned loss rate for the geometry diagnostic = gross entry-to-stop loss +
   21 bp round-trip cost hurdle.
8. Target price is solved so that after the same 21 bp round-trip cost, target
   profit is exactly **+2.0R** where R is the planned loss above.  There is no
   parameter search over reward/risk.
9. Stop is evaluated before target when both touch within one minute.
10. If neither level is touched, exit at the first available open 240 minutes
    after entry, matching the external medium-horizon impact peak used in the
    frozen mechanism.
11. Each symbol may have only one unresolved structural trade at a time in the
    diagnostic.  A later four-asset integration must use the project-wide single
    position rule and a deterministic causal arbitration rule.

## Data roles

- 2024-01-08..10: development/mechanism data already seen.
- 2024-01-22..28: untouched structural-policy test at freeze time.
- February/March windows already inspected under the simpler fixed-horizon rule
  cannot be called untouched for this modified system.

If this geometry fails on Jan-22..28, do not repair it on that window and then
call the repaired version validated.  If it succeeds, additional untouched
March dates may be reserved before any further modification.
