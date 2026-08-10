# Candidate 57 — public Slope-is-Dope 1h source tournament

## External discovery signal

The public `syuraj/freq-test` strategy
`picasso_slope_is_dope_adx_1h_2Lev_dec15_3mt.py` embeds a multi-month futures
backtest reporting 4,845.9 USDT absolute profit, 484.59% total profit, 1.53
profit factor, 11.18 trades per day and 1.42% average daily profit.  The same
source contains an earlier parameter/result block with 1,373 trades, about
10.98 trades per day, 72.25% wins, 2.28 profit factor, 178.16% total profit and
5.67% maximum drawdown.

These reports used a broad pair universe, multiple simultaneous positions,
Freqtrade stake sizing and source protections.  They are search signals only.
This tournament asks whether the trend mechanism survives the project universe,
one global slot, continuous NAV, current-NAV 3% planned-loss sizing and
realistic costs.

## Two public parameter states

The repository contains two materially different, externally supplied parameter
sets.  They must not be silently mixed.

### `claim`

The embedded result block freezes:

- ADX long/short: 39 / 20;
- close lookback long/short: 6 / 9;
- market SMA 97, fast SMA 16, slow SMA 57, RSI 10;
- rolling-low exit windows 9 / 9;
- leverage 2;
- ROI profit-ratio schedule 28.3%, 16.0%, 7.1%, 0% at
  0/132/548/961 minutes;
- stoploss profit ratio 28.9%;
- trailing positive 1.0%, offset 2.1%, activated only after offset.

### `json`

The committed current JSON freezes:

- ADX long/short: 24 / 23;
- close lookback long/short: 7 / 10;
- market SMA 120, fast SMA 15, slow SMA 46, RSI 12;
- rolling-low exit windows 8 / 9;
- leverage 2;
- ROI profit-ratio schedule 58.1%, 13.0%, 6.9%, 0% at
  0/262/580/1923 minutes;
- stoploss profit ratio 18.7%;
- trailing positive 2.5%, offset 4.8%, with source JSON's
  `trailing_only_offset_is_reached=false` behavior.

## Frozen variants

| variant | public parameter state | allowed side | source trigger |
|---|---|---|---|
| `claim_level_both` | claim | long and short | level each completed 1h candle |
| `claim_level_long` | claim | long only | level |
| `claim_level_short` | claim | short only | level |
| `json_level_both` | current JSON | long and short | level |
| `json_level_long` | current JSON | long only | level |
| `json_level_short` | current JSON | short only | level |

The public strategy evaluates entry levels rather than a one-time rising edge.
A flat account may re-enter on a later completed one-hour candle while the
condition remains true.  Each candle remains a separate source episode; no ID
splitting occurs within a candle.

## Source entry and exit

Long entry requires:

- ADX above the public long threshold;
- close above market SMA;
- both fast and slow SMA slopes positive, measured from the prior completed
  values 10 hours apart;
- close above the completed close at the public long lookback;
- RSI above 55;
- fast SMA above slow SMA;
- positive volume.

Short entry reverses every inequality and uses RSI below 55.

The source exits are preserved literally:

- long: fast SMA below slow SMA, or close below the prior rolling minimum low;
- short: fast SMA above slow SMA, or close above the prior rolling minimum low.

The unusual short rolling-low expression is not corrected in this source
fidelity test.

## Intervals and promotion

- development: **2026-04-15 through 2026-04-28 UTC**;
- untouched for at most two survivors:
  **2025-10-13 through 2025-10-19 UTC**;
- conditional 30-day continuous expansion for at most one untouched survivor:
  **2025-08-01 through 2025-08-30 UTC**.

A development survivor needs at least 14 completed trades, positive cost-after
expectancy, positive geometric daily growth, profit factor above one, no account
violation and maximum drawdown no greater than 20%.  At most the top two by
geometric daily growth proceed.

An untouched winner needs at least seven completed trades, positive expectancy
and growth, profit factor above one, no account violation and drawdown no greater
than 20%.  At most the top one proceeds.

The 30-day result passes only with:

- geometric daily growth at least 1% after costs;
- at least 30 completed trades;
- positive expectancy and profit factor above one;
- maximum drawdown no greater than 20%;
- no global-slot violation or order rejection.

## Project execution contract

- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
- completed one-hour candles built from completed minute data;
- one global pending entry or open position;
- simultaneous source candidates resolved by a causal trend-strength score and
  deterministic symbol priority;
- current-NAV 3% planned-loss sizing;
- NautilusTrader matching and continuous NAV;
- project fees, adverse slippage and funding safety;
- no symbol-specific exception;
- 2,400-minute non-binding safety ceiling;
- no source protection imported unless it is part of the declared entry,
  stop, ROI, trailing or exit logic above.

A failing public source is not rescued by threshold tuning on these intervals.
The useful outcome is either a genuinely surviving independent trend family or
a clean rejection of the external claim under the actual project constraints.
