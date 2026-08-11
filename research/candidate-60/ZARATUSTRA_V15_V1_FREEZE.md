# Candidate 60 — frozen ZaratustraV15 source diagnostic

## Why this is a distinct search-space branch

The current ZaratustraV5 study asks whether multi-timeframe directional agreement needs a clean higher-timeframe state. ZaratustraV15 is not a local V5 threshold variation. Its public source uses a different five-minute mechanism:

1. a directional-flow state combining DX, ADX, relative DMI, OBV direction, MFI and an absolute ATR guard; or
2. a close crossing outside a two-standard-deviation Bollinger band.

It also uses materially different source risk and management: 10x source leverage, -15% source stoploss, trailing activation at +10.7% source profit and trailing distance 1.2% source profit. The project adapter normalizes these to underlying price movement: 1.5% stop, 1.07% trailing activation and 0.12% trailing distance.

The external source is an exploration signal only. Every result below must come from the project NautilusTrader account.

## Preserved source semantics

- completed five-minute candles only;
- TA-Lib-style Wilder DMI, DX, ADX and ATR with period 14;
- OBV change and MFI(14) midpoint 50;
- source absolute ATR guard `< 0.2` preserved exactly rather than volatility-normalized after seeing results;
- Bollinger bands on typical price, window 20, sample standard deviation, two deviations;
- crossing is current `>` with prior `<=` for long and current `<` with prior `>=` for short;
- separate long and short source columns; a simultaneous long/short collision is ignored, matching Freqtrade's documented collision rule;
- no ROI table and no source exit signal;
- one-minute next-bar-usable trailing ordering;
- no threshold search.

The source allows seven simultaneous positions. That cannot be preserved because the project account permits one global slot. The baseline therefore uses deterministic symbol priority only; no outcome-derived strength score is invented. Arbitration is a later research problem if the entry mechanism itself survives.

## Complete scenario

- **Context:** five-minute directional-flow state or five-minute volatility breakout.
- **Interaction / transition:** source mode preserves DI levels and Bollinger crossings; edge mode requires a false-to-true directional episode and is the only policy eligible for independent-opportunity counting.
- **Entry:** next-bar-usable after the completed source candle.
- **Invalidation:** 1.5% underlying adverse move, the exact source stop after leverage normalization.
- **Objective / management:** source trailing activates after a 1.07% favorable move and trails by 0.12%; a 20% emergency objective and 2,880-minute operational safety horizon prevent an unbounded unresolved position. These safety boundaries are not claimed as public-source alpha.
- **No trade:** warmup, no source signal, long/short collision, or global slot occupied.
- **Risk and execution:** unchanged project current-NAV 3% planned-loss budget, costs, slippage, latency, matching and one continuous account.

## Frozen development cells

Scored entries: **2025-09-01 through 2025-09-14 UTC**. Three preceding days are causal warmup and three subsequent days are runoff. Warmup cannot open positions; runoff can only finish a position opened in the scored interval.

| cell | family | trigger | role |
|---|---|---|---|
| `source_combined` | DI or BB | public source | source behavior; raw trades are not an independence claim |
| `edge_combined` | DI or BB | false→true | primary independent policy |
| `edge_bb` | BB only | crossing/edge | breakout family diagnosis |
| `edge_di` | DI only | false→true | directional-flow family diagnosis |

The branch cells decompose causal roles; they are not a parameter tournament. The primary promotion policy is fixed in advance as `edge_combined`.

## Conditional policy-fresh interval

Only if `edge_combined` is mechanically valid and positive after costs with at least seven completed trades, PF > 1, positive expectancy, MDD <= 20% and largest-winner share <= 75%, run the unchanged `edge_combined` policy on **2025-11-03 through 2025-11-16 UTC**, with identical warmup and runoff.

A positive branch diagnostic cannot silently replace a failed combined primary policy on the same fresh interval. It may justify a separately frozen later test.

## Falsification

Reject the public V15 component without retuning when the edge policy is negative after costs, its apparent advantage comes from repeated level re-entry or one outlier, the source trailing winner engine does not offset stop/time losses, or a branch only looks good because the one-slot path displaced stronger trades unpredictably.

Do not rescue failure by changing ATR 0.2, MFI 50, periods 14/20, Bollinger width, 1.5% stop, 1.07% activation, 0.12% trail, or symbol priority. A failure means the source mechanism is not currently strong enough in our account and sends research to a different state/mechanism.

## Meaning of success

Policy-fresh success grants component status only. It still must be compared with the delayed post-cascade jump specialist and any surviving clean-state continuation component under one actual routing policy and one continuous one-slot NAV.
