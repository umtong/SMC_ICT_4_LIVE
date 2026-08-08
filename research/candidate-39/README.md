# Candidate 39 — Causal Auction State Router

Candidate 39 is a **non-scalping intraday** system for BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT.  It observes one-minute data, but the trading event is a completed 15-minute auction followed by three completed response minutes.  Structural targets are normally expected to resolve over roughly 30–240 minutes; safety exits are never delayed to enforce a minimum holding time.

## Decision policy

The router allows `UNRESOLVED / NO TRADE` and recognizes three scenario families:

1. **BUILD_ACCEPT_CONTINUATION** — a pre-event range boundary is broken, OI expands, signed aggressor flow agrees, participation is present, and the response accepts the boundary.
2. **CASCADE_RECLAIM_REVERSAL** — the boundary is swept while OI contracts, response bar 1 reclaims it, and a later completed bar supplies separate opposite initiative and flow flip.
3. **PEER_LED_REPRICING** — BTC or broad peer leadership establishes direction while a lagging alt accepts its own boundary with OI build, aligned flow and unused target space.

All four symbols are routed at one common completed minute.  One strongest coherent opportunity is selected; near-tied opposite-direction opportunities produce no trade.

## Geometry and management

- Context: 60 completed minutes before the 15-minute event.
- Entry reference: close of the third completed response minute.
- Continuation stop: response/boundary invalidation plus ATR buffer.
- Reversal stop: failed-attack extreme plus ATR buffer.
- Continuation objective: completed pre-event range projected from the broken boundary.
- Reversal objective: opposite edge of the completed pre-event range.
- Reject when causal reward space is less than the geometry floor.
- Maximum scheduled hold: 240 minutes; cooldown: 30 minutes.
- Planned loss budget: current continuous-account NAV × 3%, with entry/stop costs and expected execution effects retained from the reused Nautilus shell.
- Global constraint: at most one pending entry or open position across all four assets.

## Execution integrity

A live position is flattened immediately if an order is rejected, because the rejected order may be a protective child.  A position is also flattened if its planned stop was already crossed on the entry bar.  This directly corrects the unprotected-exposure failure observed in Candidate 16.

## Files

- `router.py` — pure causal state extraction, geometry and four-asset arbitration.
- `strategy.py` — Candidate 35 Nautilus execution adapter plus emergency flatten policy.
- `run.py` — Candidate 35 checksum/data/account/NAV harness with Candidate 39 identity.
- `diagnostic.py` — structural no-PnL diagnostic with interaction-time feature freezing.
- `config.json` — fixed initial hypothesis and realistic cost assumptions.
- `test_router.py`, `test_wrapper_contract.py` — causal-state and integration contracts.
- `EXTERNAL_RESEARCH.md` — Telegram, Reddit and Korean-community idea mining.
- `INTERNAL_REUSE.md` — reused components and converted failure lessons.

## Reproduction

```bash
smc4 doctor
python -m compileall -q research/candidate-39
python -m pytest -q research/candidate-39
python research/candidate-39/run.py \
  --config research/candidate-39/config.json \
  --start 2026-07-08 \
  --end 2026-07-14 \
  --cache .cache/c39-short \
  --workspace .cache/c39-short-work \
  --output artifacts/c39-short
```

The fixed first untouched short interval is 2026-07-08 through 2026-07-14.  Once its result is inspected it becomes development data.  A short result is diagnostic evidence, not a long-run success claim.

## Current evidence boundary

Local pure contracts pass before publication.  Project success is not claimed until the GitHub workflow produces checksum-backed Nautilus metrics and later untouched continuous validation survives costs, drawdown, trade-independence and frequency requirements.
