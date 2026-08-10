# Recovered squeeze source: causal clock audit

- source runs: 52
- assets: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- periods: 12
- unique causal releases: 49
- cost screen: 19 bp round trip
- one release bar creates at most one episode
- path diagnostic, not NautilusTrader NAV

## Paired source clock versus causal clock

| lifecycle | paired n | source net mean bp | causal net mean bp | source-causal bp | source better % |
|---|---:|---:|---:|---:|---:|
| 720m | 49 | 201.45 | 98.92 | 102.53 | 59.2 |
| 1440m | 49 | 194.39 | 132.04 | 62.34 | 51.0 |
| 7d | 49 | 215.00 | 176.95 | 38.05 | 42.9 |

## One global fixed slot

| clock | lifecycle | trades | trades/day | net mean bp | win % | PF | mean R |
|---|---:|---:|---:|---:|---:|---:|---:|
| source_label_left | 720m | 42 | 1.024 | 183.37 | 81.0 | 5.43 | 0.357 |
| source_label_left | 1440m | 38 | 0.927 | 138.16 | 57.9 | 2.09 | 0.329 |
| causal_completed | 720m | 42 | 1.024 | 63.67 | 47.6 | 1.58 | 0.132 |
| causal_completed | 1440m | 38 | 0.927 | 30.16 | 52.6 | 1.16 | 0.093 |

## Interpretation contract

The source and causal rows use the same completed 4h release events. Their only intended difference is when that completed bar and the 1h ATR become observable. A large source-clock advantage is implementation leakage, not market edge.

A causal positive result is still insufficient for deployment. It must be decomposed by period, asset, outside-band versus momentum-fallback entry, and intraday versus seven-day lifecycle. Only a stable first-leg state is eligible for derivatives-state refinement and later NautilusTrader execution.

- recovered source shards: 52/52.
