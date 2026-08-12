# candidate-easychart-v5

EasyChart/쉽알남 material is treated as a **complete decision process**, not as a bag of OB/FVG patterns.  v5 replaces the v3 premise “overlapping footprints are context” with a structure-first auction policy:

```text
observable structure
→ pre-existing liquidity/objective
→ interaction with the structure
→ rejection / acceptance / rotation / bounce
→ independent event-local confirmation
→ one immutable entry / stop / target plan
→ NautilusTrader execution and continuous account accounting
```

## Fixed operating contract

- Universe: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`.
- Across all four symbols, at most one pending new entry or open position.
- One entry, one full stop, one full target. No partial entry/exit/stop.
- Planned loss budget: current total NAV × 3%.
- A plan is eligible only when pre-entry gross price RR is at least `1.0R`.
- No daily trade limit and no daily loss limit.
- Entry, stop and target are fixed before submission.
- Fees, funding reserve, slippage reserve, precision, fills, portfolio and NAV remain owned by NautilusTrader and the existing project foundation.

## What changed from v3

The 2024-02-01–2024-02-14 four-symbol v3 diagnostic completed 20 trades but reduced NAV from 100,000 to 83,487.19.  The implementation and risk audit passed; the semantic premise did not.  In particular, heterogeneous OB/FVG overlaps produced 8 trades, zero wins and about `-7.68R`, while same-kind pairs were roughly neutral-to-positive in that very small sample.  This does **not** prove that OB or FVG is ineffective.  It shows that exact footprint overlap was being asked to solve the wrong problem: market context.

v5 therefore uses:

- confirmed wick pivots for horizontal liquidity and objectives;
- causal wick-anchored trend lines;
- exact-parallel channels formed by two same-side pivots and one intervening opposite pivot before a later interaction;
- rejection, acceptance, channel rotation and trend-line/level bounce as explicit state paths;
- OB/FVG only as event-local displacement footprints for rejection, rotation and bounce;
- first retest semantics and an explicit `UNRESOLVED / NO TRADE` outcome;
- diagonal structures projected to the current decision/retest time;
- channel opposite edge recalculated until entry, then frozen as the single target.

No volume threshold, RSI, score, risk multiplier, daily gate, BOCPD classifier, order-flow filter or cross-asset veto was added.  Those are deferred until a concrete trade-level ambiguity shows that OHLC structure cannot distinguish the relevant states.

## Provenance

Every plan carries rule provenance using four namespaces:

- `SOURCE_EXPLICIT`: stated or repeatedly demonstrated in the supplied PDFs/VTTs.
- `SOURCE_AMBIGUITY_TRANSLATION`: necessary deterministic conversion of a human chart action.
- `RESEARCH_HYPOTHESIS`: a falsifiable machine representation not specified by the source.
- `EXTERNAL_METHOD`: reserved for an external method that is actually used in a trading decision; currently empty.

See [`SOURCE_CONTRACT.md`](SOURCE_CONTRACT.md), [`SOURCE_CASE_LEDGER.md`](SOURCE_CASE_LEDGER.md) and [`V3_DIAGNOSTIC.md`](V3_DIAGNOSTIC.md).

## Run

The workflow uses the pinned project research image and runs `smc4 doctor` once before tests and the Nautilus diagnostic.

```bash
export PYTHONPATH=research/candidate-easychart-v5:research/candidate-easychart-v3

python research/candidate-easychart-v5/run_mtf_backtest_v5.py \
  --start 2024-02-01 \
  --end 2024-02-14 \
  --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache .cache/candidate-easychart-v5 \
  --output artifacts/candidate-easychart-v5/short-diagnostic
```

The short interval is development data used to expose semantic, causal, geometry and execution errors.  It is not evidence of future profitability and is not a holdout after rules are changed from its results.

## Evidence produced

- `metrics.json`: continuous four-symbol account outcome.
- `trade_audit.csv`: planned versus actual entry/stop/target, risk-budget use and realized net R.
- `scenario_events.jsonl`: state transitions and explicit no-trade reasons.
- `mtf_trade_windows.jsonl`: 60m/15m/5m/1m windows, structures, trigger and order events for every submitted trade.
- Nautilus `orders.csv`, `fills.csv`, `positions.csv`, `account.csv`.
- `validation.json`: joins, exit-role classification, planned-risk breaches, slippage-reserve breaches and protective-order failures.

A candidate is promoted only after trade-by-trade meaning is correct and the integrated continuous account shows enough cost-after alpha and independent opportunity to justify a longer untouched evaluation.
