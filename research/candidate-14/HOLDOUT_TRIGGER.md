# Frozen holdout execution trigger

This metadata file triggers the corrected `candidate-14.yml` frozen H01-H16 workflow.

- Strategy source remains frozen at `dfb180490b141fdafd5b5ac0b52d3dd0b842550d`.
- Diagnostic evidence remains `f859896a7c41792617bbc47b585623a5a9e55946`.
- Holdout dates and gates remain exactly those in `HOLDOUT_RESERVATION.json`.
- No strategy, threshold, detector, execution, cost, risk, stop, target, session route, semantic, or arbitration file is changed.

## Infrastructure-only correction after the first full launch

The first H01-H16 launch reached NautilusTrader. H04 then stopped while serializing the post-run event ledger because multiple independent same-bar diagnostics deliberately share the sentinel scenario id `AMBIGUOUS`. The generic evidence validator incorrectly treated those singleton terminal diagnostics as one persistent state chain.

Only `src/smc_ict_4/event_log.py` is corrected. The exact tuple

```text
scenario_id=AMBIGUOUS
event_type=AMBIGUOUS_SWEEP
previous_state=ARMED
next_state=TERMINAL
reason_code=BAR_PATH_UNRESOLVABLE
```

is now validated as an independent singleton event. Every ordinary scenario id and every near match retains strict chained-state validation. A Candidate 14 regression test proves both properties.

This correction occurs after trading decisions and does not alter orders, fills, fees, positions, NAV, sizing, market logic, or candidate selection. All H01-H16 intervals are nevertheless rerun from one commit; no artifacts are mixed across workflow commits. Successful intervals from the interrupted launch may be compared only as an implementation-invariance check, never combined with the final aggregate.
