# candidate-03 — FAR-v2

**Flow Absorption Structure Reversal** is the active independent candidate on this branch. It trades neither a wick nor an order-flow imbalance in isolation. It trades a state sequence:

```text
fresh equilibrium excursion
  -> unusually one-sided aggressive flow
  -> weak price progress and extreme rejection (absorption observation)
  -> exact trade-through of prior 10-minute opposite structure (CHoCH)
  -> reversal entry on the confirming aggregate trade
  -> signal-extreme invalidation, after-cost 3R target, or 240-minute expiry
```

The pattern detector reports observable facts. `FarReplay` owns scenario state, ordering, one-slot arbitration, and confirmation. `FarPortfolio` owns fills, costs, current-NAV loss-budget sizing, and realized NAV.

## Why v1 was discarded

The first liquidity-level classifier produced 31 trades on `2022-03-07`, only four wins, mean −0.593R, −43.63% NAV, and −7.86% daily geometric growth. Entry-touch and stricter one-minute confirmation controls remained negative. The first FAR implementation then passed its first week but failed the untouched `2025-03-17` week: 32 trades, 25% win rate, −30.25% NAV, and −5.02% daily geometric growth. Its immediate fade treated one-minute failed progress as proof of reversal; strong directional auctions continued through the stop.

FAR-v2 changes the causal sequence rather than adding a score filter. Absorption is only an observation. The trade requires the opposite side to break structure first. The same equilibrium-side excursion can consume only one attempt.

## Frozen state machine

| State | Required observation | Action |
|---|---|---|
| `IDLE` | no active episode | observe only |
| `STRETCHED_CHASE` | aggressive flow is aligned with a volume-weighted equilibrium stretch | record chase |
| `ABSORPTION_OBSERVED` | high activity, weak flow-direction progress, rejected close | create candidate only |
| `CHOCH_PENDING` | excursion age ≤120 minutes and first attempt in excursion | wait up to 15 minutes for prior-10-minute opposite structure break |
| `ENTRY_PENDING` | exact aggregate trade crosses the CHoCH boundary before invalidation | submit/open on that confirming trade |
| `POSITION_ACTIVE` | one position occupies the global candidate slot | monitor causal stop, target, and time |
| terminal | stop, target, time, pre-entry invalidation, or CHoCH expiry | close/reset |

A signal whose extreme fails before CHoCH is invalid. A signal that does not confirm in 15 minutes expires. No later signal from the same continuous side-of-equilibrium excursion is allowed.

## Frozen central specification

| Component | Value |
|---|---:|
| aggregate-flow imbalance | `abs(flow) >= 0.30` |
| activity expansion | current minute notional / median prior 360 minutes `>= 2.0` |
| causal equilibrium | 240-minute volume-weighted mean and standard second moment |
| equilibrium stretch | `abs(z) >= 0.8`, aligned with aggressive-flow sign |
| failed directional progress | flow-signed one-minute return `<= 1 bp` |
| rejection location | `>= 0.45` away from flow-direction extreme |
| fresh excursion | continuous same side of equilibrium `<= 120 minutes` |
| CHoCH | exact trade-through of the opposite boundary of the preceding 10 completed minutes |
| confirmation window | 15 minutes |
| independent episode | first attempted signal per equilibrium-side excursion; 60-minute minimum attempt spacing |
| entry | exact aggregate trade that first confirms CHoCH |
| causal stop | signal extreme plus `0.20 × ATR(60m)` buffer |
| target | price solving for `+3.0` planned-loss R after fees and impact |
| maximum hold | 240 minutes |
| risk | current total NAV × 3% planned loss budget |
| execution costs | 5 bps taker fee + 1.5 bps slippage/impact on each fill; 1 bp funding per 8h |

Quantity is always:

```text
planned loss budget = current total NAV * 0.03
quantity = planned loss budget / expected per-unit loss
```

Expected per-unit loss includes expected entry and stop fills, both fees, slippage/impact, and maximum-hold funding. Model strength never changes risk or quantity. One pending entry or open position is permitted.

## Exact-event development results

These two weeks are development evidence, not untouched validation. Both use ordered Binance USD-M aggregate trades and checksum-recorded public archives.

| BTC week | events | trades | wins | mean net R | net NAV | daily geometric growth | max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| `2022-03-07` | 14,646,755 | 9 | 6 | +1.319R | +40.80% | **+5.009%** | 2.51% |
| `2025-03-17` | 9,211,719 | 8 | 4 | +0.361R | +8.01% | **+1.106%** | 5.85% |

The exact checksums, trade rows, direction/exit breakdowns, and concentration diagnostics are committed under `results/`. The second week is the period on which FAR-v1 failed; FAR-v2 was developed by isolating that causal failure, not by disabling shorts.

## Untouched weekly validation order

Before downloading any FAR-v2 validation data, the following dates were selected by `select_validation_weeks.py` using salt `candidate-03|far-v2|BTCUSDT`, a Monday universe from 2021-01-04 through 2025-12-22, prior candidate dates excluded, and at least 180 days between selected weeks:

1. `2022-07-18`
2. `2021-12-13`
3. `2021-01-11`

The workflow opens week 2 only after week 1 passes, and week 3 only after week 2 passes. Each gate requires at least eight trades, win rate at least 45%, positive mean net R, daily geometric growth at least 1%, drawdown below 20%, and the target flag.

## Reproduction

The project environment is prebuilt; do not reinstall NautilusTrader.

```bash
smc4 doctor
PYTHONPATH=src:research/candidate-03 python research/candidate-03/test_far.py
python research/candidate-03/select_validation_weeks.py

python research/candidate-03/download_daily.py \
  --dataset aggTrades --symbol BTCUSDT \
  --start-date 2022-03-06 --end-date 2022-03-13 \
  --output .research-data/candidate-03/far-v2/dev-1 --no-extract

PYTHONPATH=src:research/candidate-03 \
  python research/candidate-03/run_far.py \
  --data .research-data/candidate-03/far-v2/dev-1/BTCUSDT-aggTrades-*.zip \
  --week-start 2022-03-07 \
  --label far-v2-development-1 \
  --output artifacts/candidate-03/far-v2/development-1
```

Every run writes `run.json`, `metrics.json`, `trades.csv`, and causally ordered `scenario_events.jsonl`. The manifest records archive SHA-256 values, event IDs, event timestamps, and the configuration hash. Raw market data is not committed.

## Known failure conditions

- A CHoCH can be a temporary pullback inside a larger informed trend; the signal-extreme stop remains necessary.
- News and liquidation gaps can cross the expected stop fill, so realized loss can exceed the planned 3% despite correct quantity calculation.
- Aggregate trades expose executed aggressor flow, not queue depletion/refill or this account's exact market impact.
- Fixed 1.5 bps impact can understate cost when NAV-scaled quantity is large relative to available depth.
- A 3R/240-minute recovery path may fail in low-volatility or fragmented auctions even when direction is eventually correct.
- The 120-minute fresh-excursion boundary and four-hour recovery horizon are logically interpretable but require untouched-week and long-period confirmation; they are not claimed invariant yet.
- The mechanism is venue-specific until the same state sequence is verified on another venue or instrument.

Long evaluation and BTC-to-ETH/SOL/XRP transfer remain blocked until all three frozen BTC validation weeks pass.
