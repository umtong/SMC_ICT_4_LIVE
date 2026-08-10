# Candidate 57 — source-faithful public RSI/BB/MACD 1h tournament

## External discovery signal

The public `syuraj/freq-test` strategy
`RSI_BB_MACD_Nov_2023_1h_2_Dec.py` reports two unusually strong long runs over
2021-01-02 through 2023-10-27 (1,027 days):

- an unlimited/broad portfolio run with 330,583 trades, 321.89 trades/day and
  5,562% total profit;
- a four-open-trade run with 24,973 trades, 24.32 trades/day, 9,513% total
  profit, roughly 76.8% win rate and reported profit factor about 1.57.

Those reports used a broad futures universe, fixed Freqtrade stake sizing and
multiple simultaneous positions. They are search signals only. This experiment
asks whether the source mechanism survives the project universe, one global
slot, continuous NAV, current-NAV 3% planned-loss sizing and realistic costs.

## Material source ambiguity

The public Python expression has a material operator-precedence behavior. For
both sides, the first ADX range can trigger independently of RSI/BB/MACD and
volume, while the second ADX range is combined with the directional and volume
conditions. The likely intended expression applies direction and volume to both
ADX ranges.

The public bot also evaluates an entry **level** every completed one-hour candle.
A flat pair can re-enter while the condition remains true. Several previous
ports converted the level into a one-time rising edge, which materially changes
frequency.

The tournament therefore freezes five interpretations:

| variant | precedence | trigger | side |
|---|---|---|---|
| `exact_level` | original Python behavior | source level | both |
| `exact_level_short` | original Python behavior | source level | short only |
| `exact_edge` | original Python behavior | rising edge | both |
| `corrected_level` | intended grouping | source level | both |
| `corrected_edge` | intended grouping | rising edge | both |

No parameter differs across variants except those three declared semantics.

## Intervals and promotion

- development: **2026-05-15 through 2026-05-28 UTC**;
- untouched comparison for at most two development survivors:
  **2025-11-03 through 2025-11-09 UTC**;
- conditional 30-day continuous expansion for at most one untouched survivor:
  **2025-09-01 through 2025-09-30 UTC**.

A development survivor must have at least 14 completed trades, positive
cost-after expectancy, positive geometric daily growth, no global-slot/order
violation and maximum drawdown no greater than 20%. At most the top two by
geometric growth proceed.

An untouched winner must have at least seven completed trades, positive
cost-after expectancy and growth, profit factor greater than one, no account
violation and drawdown no greater than 20%. At most the top one proceeds to 30
days.

The 30-day result is a pass only with:

- geometric daily growth at least 1% after costs;
- completed trades at least 30;
- positive expectancy and profit factor greater than one;
- maximum drawdown no greater than 20%;
- no global-slot violation or order rejection.

## Frozen public source contract

- completed one-hour candles;
- long directional state: RSI(22) > 50, close between BB(16) middle and upper,
  MACD above signal and bullish candle with source wick condition;
- short directional state: RSI(17) < 50, close between BB(20) lower and middle,
  MACD below signal and bearish candle with source wick condition;
- ADX(14) ranges exactly as published;
- shifted source volume means: 38 long, 20 short;
- source effective leverage semantics: 5x;
- source stoploss profit ratio: 31.7%, normalized to underlying distance;
- public ROI profit-ratio schedule: 18.4%, 14.0%, 7.3%, then 0 at
  0/416/933/1982 minutes;
- public trailing activation 2.2% and positive 1.0%, normalized by 5x;
- public EMA/ATR/volume exit conditions: EMA91/EMA147, ATR20 × 3.8/5.0,
  shifted volume periods 19/41;
- no symbol-specific exception or result-derived parameter.

## Project execution contract

- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
- one global pending entry or open position;
- current-NAV 3% planned-loss sizing;
- NautilusTrader matching and continuous NAV;
- project fees, adverse slippage and funding safety;
- completed data only;
- source maximum holding safety ceiling 2,400 minutes;
- source-level simultaneous candidates are resolved by the existing causal
  score and deterministic symbol priority.

A failed public source is not repaired by indicator threshold tuning in these
intervals. The useful outcome is either a surviving external alpha family or a
clear rejection of the public claim under the project's actual constraints.
