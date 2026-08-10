# Recovered public squeeze system

- immutable source: `jicheolha/crypto-trading-bot@99cfa582b239fd9c59a5ac92618a3e36bb73ed76`
- purpose: recover exact executable rules and optimization provenance before transfer
- Optuna studies recovered: 1
- one shared parameter policy across BTC/ETH/SOL/XRP: `True`

## Published asset logs

| asset | trades | wins | losses | win % | repeated entries beyond first matching setup | signal / ATR | BB | KC | stop / target |
|---|---:|---:|---:|---:|---:|---|---|---|---|
| BTCUSD | 128 | 87 | 41 | 68.0 | 7 | 4h/1h | 19/2.47 | 17/2.38 | 3.45/4.0 |
| ETHUSD | 134 | 85 | 49 | 63.4 | 12 | 4h/1h | 19/2.47 | 17/2.38 | 3.45/4.0 |
| SOLUSD | 90 | 61 | 29 | 67.8 | 5 | 4h/1h | 19/2.47 | 17/2.38 | 3.45/4.0 |
| XRPUSD | 56 | 44 | 12 | 78.6 | 7 | 4h/1h | 19/2.47 | 17/2.38 | 3.45/4.0 |

## Recovered 4h / 1h optimization study

### `tf_4h_1h`

- trials: 166 (87 complete)
- best trial: 138
- best value: 9.242131617043754

```json
{
  "atr_period": 18,
  "atr_stop_mult": 2.7073618568345394,
  "atr_target_mult": 4.238633598811262,
  "bb_period": 37,
  "bb_std": 1.7845475023385324,
  "kc_atr_mult": 2.5343188638600815,
  "kc_period": 26,
  "min_squeeze_bars": 1,
  "min_volume_ratio": 0.7770705861074451,
  "momentum_period": 28,
  "rsi_overbought": 83,
  "rsi_oversold": 32,
  "rsi_period": 25,
  "setup_validity_bars": 12,
  "volume_period": 38
}
```

## Causal transfer contract

The next experiment is not a parameter tournament. It freezes the recovered source and asks whether the completed 4h compression-release episode has a stable first-leg edge on Binance perpetuals. One release may create at most one independent entry. Price-only source behavior is measured first; OI, taker flow, basis and peer breadth are then used only to distinguish sponsored continuation, failed breakout and exhaustion reversal.

A negative aggregate result does not automatically discard every component. The experiment must separately report the opportunity engine, first-leg winner engine, repeated-entry contribution, stale multi-day holding contribution, and the state variables associated with losses and missed opportunities.
