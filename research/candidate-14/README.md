# Candidate 14 — Combined Cross-Market and Session Auction Portfolio

Candidate 14 runs two economically distinct scenario modules through one NautilusTrader portfolio:

1. **SCDAM core** — Candidate 13's synchronized four-market failed-auction reversal and accepted-auction continuation semantics.
2. **Session I7** — Candidate 12 I7's frozen BTC Asia/London completed-session rejection, acceptance, reacceptance and protected FVG routes from commit `036c0e8302c3826aa293f6037405a84fc7118ae8`.

Both modules share one `GlobalCandidateMutex`, one current-NAV exact 3% planned-loss sizer, one margin account and one position/pending-entry slot. They never run separate NAV curves and their returns are not added after the fact.

## Research path

### Development v1 — rejected

Generic trend resumption, originator/laggard transfer, path-only confirmation and AAC immediate execution increased frequency but produced 12 trades with 5 wins and 7 losses. The logic failed despite clean implementation audits. Evidence is retained as `development-v1-aggregate.json` and `development-v1-RESULT.md`.

### Development v2 — selective but incomplete

The Candidate 13 core was restored and FAR received a causal displacement-failure execution fallback. The result was 6 trades, 5 wins, 1 loss, 0.9032% daily geometric growth and a 2.456 payoff ratio. Trade-level diagnosis showed:

- the displacement execution converted a previously unfilled W10 plan into a winner;
- the only liquidity-leader-catch-up trade was the W13 loss.

Leader catch-up was removed rather than tuned. Evidence is retained as `development-v2-aggregate.json` and `development-v2-RESULT.md`.

### Development v3 — current

The preserved SCDAM core and displacement execution are combined with the already implemented Candidate 12 I7 session state machine. No Candidate 12 rule was rewritten or fitted to Candidate 14 dates; its source is copied by exact Git blob.

## Scenario independence

```text
SCDAM core
  completed regional range
  -> pre-existing external liquidity traded through
  -> local reclaim or outside acceptance
  -> local structure displacement
  -> synchronized four-market semantic approval
  -> independent external target

Session I7
  completed Asia or London range
  -> boundary raid, acceptance, failed acceptance or FVG mitigation
  -> route-specific reclaim/MSS/confirmation
  -> session structural objective
```

The modules are alternative scenario branches, not stacked indicators. A same-minute conflict is resolved by the existing deterministic global candidate arbitration before any order is submitted.

## Execution and risk

- Account NAV is read from Nautilus at each submission.
- Planned loss budget is `NAV × 3%`.
- Quantity uses full entry-to-stop price risk plus the route's frozen fee/slippage reserves.
- SCDAM passive orders remain post-only.
- I7's one-bar protected FVG limit remains non-post-only because its loss budget reserves taker entry cost and two ticks of slippage.
- Take profit is post-only limit GTC; stop is stop-market GTC.
- Pending new entry plus open position is globally limited to one.

## Validation status

W10-W14 are development diagnostics already exposed in earlier candidates. They can reject implementation or scenario logic but can never establish success. If the combined diagnostic survives, source and protocol are frozen before newly predeclared non-overlapping intervals are evaluated.

## Reproduction

```bash
smc4 doctor
export PYTHONPATH="$PWD/research/candidate-14:$PWD/src"
python -m unittest discover -s research/candidate-14 -p 'test_*.py' -v
bash research/candidate-14/run_week.sh W10
python research/candidate-14/aggregate.py \
  --results research/candidate-14/results \
  --protocol research/candidate-14/protocol.json \
  --output research/candidate-14/aggregate.json
```

NautilusTrader is the only backtest, execution and account engine.
