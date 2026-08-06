# Authoritative backtest contract

Candidate 01 has one authoritative performance path:

- engine: pinned NautilusTrader from the project research image;
- environment check: `smc4 doctor` before every run;
- weekly runner: `intrinsic_external_liquidity_v4_nautilus_week.py`;
- continuous long runner: `intrinsic_external_liquidity_v4_nautilus_period.py`;
- execution adapter: `nautilus_plan_backtest.py`.

The SMC/ICT detector and router may produce causal plans and diagnostics, but
they do not fill orders or calculate PnL, NAV, commissions, margin, positions,
or liquidation state. Those outputs are owned by NautilusTrader.

Earlier artifacts produced by `impact_regime_probe.simulate`,
`portfolio_probe.simulate`, or any other local execution ledger are diagnostic
history only. They are not admissible evidence for weekly gates, long gates, or
the project objective and must be rerun through the authoritative path before
being cited.

## Fixed execution assumptions

- current NautilusTrader portfolio equity is the sizing base;
- planned loss budget is exactly 3% per accepted trade;
- all-in stressed cost is 7 bps per side;
- one pending entry or position globally;
- structural stop and routed liquidity target come from the candidate logic;
- each completed-event signal is submitted on the next completed event;
- bracket, fees, margin, positions, account equity and reports are engine-owned;
- the long evaluation is one continuous engine run, with no stitched weekly NAV.

## Commands

```bash
smc4 doctor
python research/candidate-01/intrinsic_external_liquidity_v4_nautilus_week.py \
  --week 2023-06-19

python research/candidate-01/intrinsic_external_liquidity_v4_nautilus_period.py \
  --start 2024-01-01 --end 2024-04-01
```
