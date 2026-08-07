# Candidate 14 — SCDAM + Session Auction with Four-Market Semantics

Candidate 14 runs two independently causal scenario generators through one NautilusTrader portfolio:

1. **SCDAM core** — Candidate 13's synchronized four-market failed-auction reversal and accepted-auction continuation semantics, with Candidate 14's validated displacement-failure execution fallback.
2. **Session I7 generator** — Candidate 12 I7's frozen BTC Asia/London completed-session rejection, acceptance, reacceptance and protected FVG routes from commit `036c0e8302c3826aa293f6037405a84fc7118ae8`.

Every completed session plan must now pass the same four-market economic state machine as the SCDAM core before it can enter the shared global candidate arbitration.

## Development history

### V1 — rejected

Generic trend resumption, originator/laggard transfer, path-only confirmation and AAC immediate execution produced 12 trades with 5 wins and 7 losses. Implementation audits were clean; the logic was bad.

### V2 — selective, just below target

The Candidate 13 core was restored and FAR received a full-displacement-traversal execution fallback. The result was 6 trades, 5 wins, 1 loss, 0.9032% daily geometric growth and a 2.456 payoff ratio. The only leader-catch-up trade lost, so that state was removed.

### V3 — frequency solved, semantic breadth too loose

The complete I7 session module was inserted into the same Nautilus account and global slot. Fresh provenance-verified diagnostics produced:

- 35 days;
- NAV multiple 1.3915;
- 0.9485% daily geometric growth;
- 12 trades, 8 wins, 4 losses;
- payoff ratio 1.9449;
- all safety audits passed.

The four session losses were two fresh Asia reacceptances, one Asia high acceptance and one London high rejection. Yet earlier I7 evidence contains profitable continuation and rejection routes. Deleting route names would therefore memorize the current dates rather than identify the common failure cause.

V3 evidence is retained in `development-v3-aggregate.json` and `development-v3-RESULT.md`.

## V4 hypothesis

The I7 local state machine answers whether a completed BTC session boundary produced a valid local plan. It does not by itself answer whether the four-market auction supports that plan. V4 reconstructs each plan's causal observation interval and applies the already-frozen four-market semantic gate:

```text
session failed auction
  raid close -> completed confirmation
  -> evaluate as FAR

first session acceptance
  bullish FVG formation -> defended retest decision
  -> evaluate as AAC

fresh reacceptance
  prior accepted-auction failure back inside -> fresh reacceptance decision
  -> evaluate as AAC
```

The start times are taken from the unchanged I7 plan or event log. Missing context fails closed. No lookback length, price threshold, route whitelist, stop, target or risk parameter is added.

## Shared execution and risk contract

- Both generators feed one `GlobalCandidateMutex`.
- Pending new entry plus open position is globally limited to one.
- NAV is read from Nautilus at each submission.
- Planned loss budget is exactly `current NAV × 3%`.
- Quantity includes the route's frozen entry-to-stop risk, fees and slippage reserve.
- SCDAM passive limits remain post-only.
- I7's one-bar protected FVG limit remains non-post-only because its frozen loss budget reserves taker entry and two ticks of slippage.
- Take profit is post-only limit GTC; stop is stop-market GTC.
- NautilusTrader exclusively owns orders, fills, fees, margin, positions and NAV.

## Validation status

W10-W14 remain development diagnostics already exposed in prior candidates. They can reject V4 but can never prove success. If V4 survives, source and protocol are frozen before newly predeclared, non-overlapping intervals are evaluated.

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
