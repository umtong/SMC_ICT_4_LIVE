# Candidate 14 — Session FAR Price-Discovery Semantics

Candidate 14 runs the preserved Candidate 13 synchronized four-market SCDAM core and the frozen Candidate 12 I7 BTC session auction through one NautilusTrader portfolio, one current-NAV 3% loss sizer and one global pending-entry/open-position slot.

## Evidence path

- **V1:** broad transfer and immediate AAC execution failed logically: 12 trades, 5 wins, 7 losses.
- **V2:** restored core plus displacement-failure execution: 6 trades, 5 wins, 1 loss, 0.9032% daily geometric growth.
- **V3:** unconditioned I7 combination solved frequency but admitted four session losses: 12 trades, 8 wins, 4 losses, 0.9485% daily growth.
- **V4:** every I7 plan used the unchanged SCDAM FAR/AAC market gate: 6 trades, 6 wins, 1.0551% daily growth, NAV 1.4439x, zero trade-path drawdown.
- **V5:** completed-session FAR price discovery: 8 trades, 8 wins, 1.3005% daily growth, NAV 1.5718x, zero closed-trade drawdown. The only failed development gate was the absolute 10-trade count.

W10-W14 are permanently development-only. Their exact V5 evidence is preserved at commit `f859896a7c41792617bbc47b585623a5a9e55946`.

## Frozen strategy

SCDAM and session AAC are unchanged. For an already-complete I7 failed-session-auction plan only:

```text
completed I7 five-minute rejection / MSS
-> all three peers transfer in the plan direction
-> candidate causal path passes existing efficiency and displacement floors
-> event direction rank is in the top half
-> countertrend transfer, or aligned trend resumption with top-half trailing rank
-> shared global arbitration and exact costed 3% NAV sizing
```

The I7 route substitutes only for the duplicated terminal one-minute impulse. It does not substitute for market synchronization, path quality or price-discovery rank.

## Why weekly evidence is no longer accepted as long-horizon proof

The previous aggregate restarted every week from 100,000 USDT, multiplied independent weekly NAV ratios and took the maximum of weekly drawdowns. That is useful for mechanism diagnosis but it is not one recoverable account path. It removes cross-week state, position/order continuity, loss clustering and true compounding quantity changes.

`LONG_HORIZON_FAILURE_ANALYSIS.md` records the repository-wide evidence behind this correction.

## Frozen contiguous holdout

Before downloading any L1 outcome, `CONTIGUOUS_HOLDOUT_RESERVATION.json` fixed:

- one NautilusTrader engine and account;
- `2026-05-11` through `2026-08-03`;
- 84 continuous calendar days / 12 complete weeks;
- no intermediate NAV, state, order or liquidity-pool reset;
- no strategy, threshold, execution, cost, risk or arbitration change.

The root `aggregate.json` and `RESULT.md` are authoritative only after the L1 workflow completes. Calendar-week records are slices of the one account path, not separately capitalized backtests.

## Continuous gate

- after-cost NAV geometric growth per calendar day at least 1%;
- at least 18 closed trades;
- at least 9 active calendar weeks out of 12;
- observed win rate at least 80%;
- 95% Wilson win-rate lower bound at least 50%;
- payoff ratio at least 1.2;
- continuous realized trade-path drawdown at most 20%;
- no one week above 35% of positive log growth;
- no more than two consecutive empty weeks;
- all evidence, risk-budget, global-slot, partial-fill, liquidation and engine audits passing.

## Execution contract

- NautilusTrader exclusively owns orders, fills, fees, margin, positions and NAV.
- Pending new entry plus open position is globally limited to one across BTC, ETH, SOL and XRP.
- Planned loss budget is current NAV times 3%, including each route's frozen entry-to-stop risk, fees and slippage reserve.
- SCDAM passive limits are post-only; I7 protected FVG limits retain their frozen non-post-only semantics.
- Take profit is post-only limit GTC; stop is stop-market GTC.

```bash
smc4 doctor
export PYTHONPATH="$PWD/research/candidate-14:$PWD/src"
python -m unittest discover -s research/candidate-14 -p 'test_*.py' -v
bash research/candidate-14/run_week.sh L1
python research/candidate-14/continuous_aggregate.py \
  --result-dir research/candidate-14/results/L1 \
  --protocol research/candidate-14/protocol.json \
  --output research/candidate-14/aggregate.json
```
