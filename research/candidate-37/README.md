# Candidate 37 — Burst Shape & Propagation Router

Candidate 37 does not extend the fixed quarter-hour auction thesis from Candidate 35. It reuses only the verified data, four-symbol synchronization, NautilusTrader execution, single global position slot, cost model and 3% current-NAV risk sizing.

## Hypothesis

A large one-minute move is not one state. The router first asks what generated the burst.

1. **Abrupt synchronous common shock (`SYNC_PROPAGATION`)** — at least three of BTC, ETH, SOL and XRP activate together, directions agree and the common move remains accepted. The system waits one or two completed bars and trades the laggard only after it begins accepting the common factor.
2. **Endogenous isolated ramp failure (`ENDOGENOUS_EXHAUSTION`)** — one asset's activity accelerates into an isolated jump while peers do not confirm. The system trades only after the extreme fails, the opposite response appears and the pre-burst balance leaves at least the configured reward/risk.
3. Everything else is **`UNRESOLVED / NO TRADE`**.

This is motivated by external evidence that exogenous and endogenous jumps have different temporal activity shapes, order flow contains self-exciting core and reaction components, and common shocks diffuse across crypto assets at different speeds. External claims are only idea sources; this repository's causal diagnostics and NautilusTrader account results are the evidence that matters.

## Causal contract

- Decisions use completed one-minute OHLCV bars only.
- ATR and volume baselines exclude the candidate shock bar.
- Four symbols must share the exact latest timestamp and history length.
- A shock is evaluated only one or two bars later; no future bar enters classification.
- Forward bars in `diagnostic.py` are labels written after a decision is fixed. They are not order, fill, PnL or NAV simulation.
- Repeated signals from one event are collapsed by episode identity and a 30-minute causal lockout.
- Score selects among valid routes; it never changes the 3% risk budget.

## First-stage diagnostic

The workflow `.github/workflows/candidate-37-short-diagnostic.yml` reuses the checksum-verified four-symbol inputs produced by Candidate 35 for 2026-07-01 through 2026-07-07. It runs unit tests and writes:

- `burst_diagnostic.json` — frequency, state mix, geometry, path outcomes, markouts and claim boundary;
- `burst_routes.csv` — one row per globally selected independent route;
- `console.log`.

A positive result only permits a short NautilusTrader execution diagnostic. It is not a profitability claim. Threshold changes after viewing this week make the week development data; any revised system must move to untouched data.

## Reproduction

```bash
smc4 doctor
python -m unittest discover -s research/candidate-37 -p 'test_router.py' -v
python research/candidate-37/diagnostic.py \
  --input-root artifacts/c37-short-input \
  --config research/candidate-37/config.json \
  --output artifacts/c37-short-diagnostic
```
