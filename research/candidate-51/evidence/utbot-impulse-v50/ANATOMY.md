# Public UTBot / ATR-impulse causal anatomy

This is a signal-mechanism diagnostic, not a NAV backtest.

- Source result files: 36
- Raw horizon observations: 5436
- Global non-overlap observations: 2496

## Development ranking (15 bps round-trip screen)

1. `impulse_only_2atr` h=24: n=34, mean=108.99 bps, win=52.9%, PF=1.8762282740566132
2. `public_vectorized_no_ema` h=12: n=32, mean=88.95 bps, win=53.1%, PF=1.9558220966267272
3. `public_vectorized_no_ema` h=24: n=27, mean=66.60 bps, win=51.9%, PF=1.4617164386828776
4. `impulse_only_2atr` h=12: n=42, mean=64.69 bps, win=50.0%, PF=1.5207212574626914
5. `recursive_utbot_full` h=12: n=18, mean=61.95 bps, win=55.6%, PF=1.809333501501429
6. `public_vectorized_full` h=12: n=30, mean=58.52 bps, win=53.3%, PF=1.6224604718517985
7. `public_vectorized_no_volume` h=12: n=30, mean=58.52 bps, win=53.3%, PF=1.6224604718517985
8. `public_vectorized_no_ema` h=8: n=35, mean=53.68 bps, win=45.7%, PF=1.6049837025476978
9. `public_vectorized_no_adx` h=24: n=26, mean=46.78 bps, win=53.8%, PF=1.3636559624535005
10. `impulse_only_2atr` h=8: n=47, mean=34.09 bps, win=46.8%, PF=1.304739646966632
11. `recursive_utbot_full` h=8: n=19, mean=29.99 bps, win=52.6%, PF=1.411279663081613
12. `public_vectorized_no_ema` h=4: n=37, mean=27.85 bps, win=43.2%, PF=1.3964855516579378

## Frozen development winners on other splits

- development `impulse_only_2atr` h=12: n=42, mean=64.69 bps, win=50.0%, PF=1.5207212574626914
- forward `impulse_only_2atr` h=12: n=22, mean=45.94 bps, win=50.0%, PF=2.105541567725202
- stress `impulse_only_2atr` h=12: n=15, mean=51.04 bps, win=40.0%, PF=1.4813197476368125
- development `impulse_only_2atr` h=24: n=34, mean=108.99 bps, win=52.9%, PF=1.8762282740566132
- forward `impulse_only_2atr` h=24: n=18, mean=-13.94 bps, win=44.4%, PF=0.8821917562812299
- stress `impulse_only_2atr` h=24: n=12, mean=150.18 bps, win=58.3%, PF=3.4859857503044
- development `public_vectorized_full` h=12: n=30, mean=58.52 bps, win=53.3%, PF=1.6224604718517985
- forward `public_vectorized_full` h=12: n=15, mean=17.22 bps, win=40.0%, PF=1.2243456964017194
- stress `public_vectorized_full` h=12: n=9, mean=225.75 bps, win=55.6%, PF=10.81155102330394
- development `public_vectorized_no_ema` h=12: n=32, mean=88.95 bps, win=53.1%, PF=1.9558220966267272
- forward `public_vectorized_no_ema` h=12: n=16, mean=36.13 bps, win=43.8%, PF=1.790691710039186
- stress `public_vectorized_no_ema` h=12: n=11, mean=226.17 bps, win=63.6%, PF=7.590173830692335
- development `public_vectorized_no_ema` h=24: n=27, mean=66.60 bps, win=51.9%, PF=1.4617164386828776
- forward `public_vectorized_no_ema` h=24: n=15, mean=-21.90 bps, win=46.7%, PF=0.8127995261378251
- stress `public_vectorized_no_ema` h=24: n=10, mean=297.47 bps, win=80.0%, PF=45.79351876138823
- development `recursive_utbot_full` h=12: n=18, mean=61.95 bps, win=55.6%, PF=1.809333501501429
- forward `recursive_utbot_full` h=12: n=8, mean=-81.74 bps, win=25.0%, PF=0.27471034695327556
- stress `recursive_utbot_full` h=12: n=8, mean=191.77 bps, win=50.0%, PF=8.408656707541343
