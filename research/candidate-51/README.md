# Candidate 51 — open-book trend acceleration + NR7 router

Candidate 51 starts from a reused, already exercised NautilusTrader four-symbol
execution/account shell. It does **not** claim Candidate 35/47 alpha. The active
policy is replaced by two independently diagnosable scenario families:

1. `ICHI_FAN_ACCELERATION_CONTINUATION` — a causal adaptation of the public
   Freqtrade `ichiV1/ichiV2` family: Heikin-Ashi/EMA agreement across 5m–8h
   horizons, price outside a causal Ichimoku cloud, accelerating 1h/8h fan
   magnitude, recent progress, participation and cross-asset arbitration.
2. `NR7_RANGE_EXPANSION` — the adjacent-bucket breakout of the narrowest
   completed 15-minute range in seven bars, admitted only with background trend
   and participation support.

The router sees completed one-minute observations only. NautilusTrader retains
orders, latency, fees, slippage, contingent exits, positions, liquidation and
continuous NAV. Across BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT there is one global
pending-entry/position slot. Planned loss remains current NAV × 3%, including
entry/stop fees, adverse slippage reserve and funding reserve.

## Reused external material

- `ichiV1`: <https://github.com/remiotore/ccxt-freqtrade/blob/master/strategies/ichi_v1.py>
- public `ichiV2` backtest claim used only as an exploration lead, never as
  evidence for Candidate 51: <https://gist.github.com/vjaykrsna/3aa41ada83ea890721e27ccda02c1d64>
- Freqtrade strategy repository: <https://github.com/freqtrade/freqtrade-strategies>

## Run

```bash
uv run --with pytest python -m pytest -q research/candidate-51/test_router.py
uv run python research/candidate-51/launch.py \
  --config research/candidate-51/config.json \
  --start 2026-07-22 --end 2026-07-28 \
  --cache .cache/candidate-51 \
  --output artifacts/candidate-51/development \
  --workspace .work/candidate-51
```

No performance claim is accepted until the branch workflow produces reproducible
four-asset, one-account Nautilus metrics. Any period inspected and then used to
change rules is development data.
