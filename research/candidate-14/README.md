# Candidate 14 — Session FAR Price-Discovery Semantics

Candidate 14 runs the preserved Candidate 13 synchronized four-market SCDAM core and the frozen Candidate 12 I7 BTC session auction through one NautilusTrader portfolio, one current-NAV 3% loss sizer and one global pending-entry/open-position slot.

## Evidence path

- **V1:** broad transfer and immediate AAC execution failed logically: 12 trades, 5 wins, 7 losses.
- **V2:** restored core plus displacement-failure execution: 6 trades, 5 wins, 1 loss, 0.9032% daily geometric growth.
- **V3:** unconditioned I7 combination solved frequency but admitted four session losses: 12 trades, 8 wins, 4 losses, 0.9485% daily growth.
- **V4:** every I7 plan used the unchanged SCDAM FAR/AAC market gate: 6 trades, 6 wins, 1.0551% daily growth, NAV 1.4439x, zero trade-path drawdown. Only the 10-trade diagnostic requirement failed.

V4 removed all four V3 losses, but it also rejected two W12 session-rejection winners because their final one-minute return was weak. Their complete I7 five-minute failed-auction route, peer unanimity, top-half event rank and standardized causal displacement were strong. The rejected W14 losing rejection was last in both event and trailing price discovery.

## V5 hypothesis

SCDAM and session AAC are unchanged. For an already-complete I7 failed-session-auction plan only:

```text
completed I7 five-minute rejection / MSS
-> all three peers transfer in the plan direction
-> candidate causal path passes existing efficiency and displacement floors
-> event direction rank is in the top half
-> countertrend transfer, or aligned trend resumption with top-half trailing rank
-> shared global arbitration and exact costed 3% NAV sizing
```

The I7 route substitutes only for the duplicated terminal one-minute impulse. It does not substitute for market synchronization, path quality or price-discovery rank. No threshold or route name is fitted.

## Execution contract

- NautilusTrader exclusively owns orders, fills, fees, margin, positions and NAV.
- Pending new entry plus open position is globally limited to one across BTC, ETH, SOL and XRP.
- Planned loss budget is current NAV times 3%, including each route's frozen entry-to-stop risk, fees and slippage reserve.
- SCDAM passive limits are post-only; I7 protected FVG limits retain their frozen non-post-only semantics.
- Take profit is post-only limit GTC; stop is stop-market GTC.

## Validation status

W10-W14 remain development diagnostics and cannot prove success. V5 must first survive the one-change comparison. If it does, code and protocol are frozen before deterministic, non-overlapping holdout weeks are selected and run.

```bash
smc4 doctor
export PYTHONPATH="$PWD/research/candidate-14:$PWD/src"
python -m unittest discover -s research/candidate-14 -p 'test_*.py' -v
bash research/candidate-14/run_week.sh W10
```
