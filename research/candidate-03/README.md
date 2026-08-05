# candidate-03 — Flow Absorption Reversal

This branch is one independent complete candidate. It does not depend on another research branch.

## Decision record

### Iteration 01 — liquidity-sweep classifier: rejected

The first implementation classified a previously formed liquidity level after breach as rejection, acceptance, or no-trade using one-minute candle geometry and Binance's one-minute taker-buy-volume field. It was intentionally screened on the first precommitted BTC week before any later week was opened.

After taker fees of 5 bps per fill, 1.5 bps slippage/impact per fill, funding, and 3% NAV loss-budget sizing, the week produced:

- 31 trades, 4 wins, 27 losses
- 12.90% win rate
- mean net result −0.593R; median −0.987R
- net NAV return −43.63%; daily geometric growth −7.86%
- maximum drawdown 44.30%

Entry-touch, directional-close, close-location, and flow-confirmation controls all remained negative. Exact aggregate-trade analysis also showed that the level-selection premise did not separate continuation from absorption. This was a logic failure, not an engine or data failure, so the iteration was stopped. Its implementation and first-week evidence remain in the branch for falsification history.

### Iteration 02 — Flow Absorption Reversal: active candidate

The surviving causal hypothesis is that a directional market-order chase can be temporarily exhausted when all of the following are observable at a completed one-minute auction:

1. Price is stretched from a causal four-hour volume-weighted equilibrium.
2. Aggressive taker notional is aligned with that stretch and is unusually imbalanced.
3. Minute notional activity is elevated relative to the prior six-hour activity distribution.
4. Despite the aggressive flow, directional price progress is negligible and the close rejects the flow-direction extreme.
5. The strategy trades against the chase on the first aggregate trade strictly after the completed minute.

This is an order-flow absorption scenario, not a standalone candle pattern. The detector computes observable facts; the scenario/portfolio layer owns state transitions, entry, invalidation, target, and NAV accounting.

## Frozen state machine

```text
IDLE
  -> STRETCHED_CHASE       aggressive flow aligned with equilibrium stretch
  -> ENTRY_PENDING         high activity but failed directional progress
  -> POSITION_ACTIVE       first aggregate trade after minute close
  -> CLOSED                target, causal stop, time expiry, or run end
```

No signal score changes the risk fraction or quantity. One pending entry or open position is allowed across the candidate.

## Frozen central specification

The values below were fixed from market logic and the middle of a broad first-week performance plateau before opening the second or third validation week.

| Component | Frozen value |
|---|---:|
| aggressive notional imbalance | `abs(flow) >= 0.30` |
| activity expansion | current minute notional / median prior 360 minutes `>= 2.0` |
| equilibrium stretch | absolute 240-minute volume-weighted z-score `>= 0.8` |
| chase alignment | aggressive-flow sign equals equilibrium-stretch sign |
| failed progress | flow-signed minute return `<= 1.0 bp` |
| rejection location | `>= 0.45` away from flow-direction extreme |
| entry | first aggregate trade strictly after the signal-minute close |
| stop | beyond signal-minute extreme by `0.20 × ATR(60m)` |
| target | price solving for `+2.0` net planned-loss R after fees and impact |
| maximum holding time | 60 minutes |
| independent-episode cooldown | 60 minutes |
| risk | current total NAV × 3% planned loss budget |
| execution costs | 5 bps taker fee + 1.5 bps slippage/impact on each fill; 1 bp funding per 8h |

The equilibrium variance is the standard causal weighted second moment:

```text
mean     = sum(price * volume) / sum(volume)
variance = sum(price^2 * volume) / sum(volume) - mean^2
```

The current minute is known at its close and may enter the equilibrium calculation. Its activity is compared only with prior minutes.

## Precommitted BTC validation order

The weeks were selected before their candidate results were viewed and are separated by at least 180 days:

1. `2022-03-07` — discovery and first gate
2. `2025-03-17` — untouched second gate
3. `2023-08-28` — untouched third gate

The workflow downloads and evaluates week 2 only after week 1 passes, and week 3 only after week 2 passes. No later-week result is used to change this frozen specification.

## First-week frozen-result evidence

Local exact-event replay after the weighted-variance bug was corrected and after the code was split into detector, portfolio, replay, and metrics modules produced:

- 14,646,755 ordered Binance USD-M aggregate trades
- 19 accepted trades from 25 qualifying signals
- 11 wins, 8 losses; win rate 57.89%
- mean net result +0.613R; median +0.570R
- after-cost NAV return +39.15%
- daily geometric NAV growth +4.83%
- maximum drawdown 9.66%
- 9 targets, 7 stops, 3 time exits
- largest winner 10.54% of positive R; top three 31.61%

The central point was not selected as the best backtest cell. In the local neighboring threshold set around the frozen point, 14 of 16 settings exceeded 1% daily geometric growth and all 16 were positive; exit-distance and holding-time controls also showed a broad positive region. These are first-week diagnostics only, not substitutes for the untouched gates.

## Reproduction

The project environment is prebuilt. Do not reinstall NautilusTrader.

```bash
smc4 doctor
PYTHONPATH=src:research/candidate-03 \
  python research/candidate-03/test_far.py

python research/candidate-03/download_daily.py \
  --dataset aggTrades --symbol BTCUSDT \
  --start-date 2022-03-06 --end-date 2022-03-13 \
  --output .research-data/candidate-03/week-1 --no-extract

PYTHONPATH=src:research/candidate-03 \
  python research/candidate-03/run_far.py \
  --data .research-data/candidate-03/week-1/BTCUSDT-aggTrades-*.zip \
  --week-start 2022-03-07 \
  --label btc-week-1-2022-03-07-far-v1 \
  --output artifacts/candidate-03/far/week-1

python research/candidate-03/gate.py \
  artifacts/candidate-03/far/week-1/metrics.json \
  --minimum-trades 8 --minimum-win-rate 0.45 \
  --minimum-daily-growth 0.01 --require-target
```

Each run writes `run.json`, `metrics.json`, `trades.csv`, and a causally validated `scenario_events.jsonl`. The run manifest records archive SHA-256 values and event counts. Raw market data is not committed.

## Known failure conditions

- Aggressive flow can be informed rather than exhausted; a trend acceleration after apparent rejection can stop the position.
- News or liquidation cascades can jump beyond the causal stop. Actual loss can then exceed the planned 3% despite correct quantity calculation.
- Public aggregate trades expose executed aggressor flow, not L2 queue depletion, refill, or this account's exact market impact.
- The fixed 1.5 bps impact assumption can understate cost when NAV-based quantity is large relative to available liquidity.
- Quiet or fragmented regimes can reduce independent opportunities below the rate needed for the growth target.
- The mechanism is measured on Binance USD-M aggressor flow; direct transfer to another venue is not assumed.
- A one-minute completed-auction decision intentionally sacrifices sub-minute entry speed in exchange for causal observability.

Long evaluation and cross-instrument testing are permitted only after all three frozen BTC weekly gates pass.
