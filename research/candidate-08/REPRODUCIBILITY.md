# Reproducibility contract

## Environment

The repository's pinned prebuilt environment is authoritative: Python 3.13 and NautilusTrader
1.230.0. Candidate 08 does not install dependencies or implement a replacement backtester.

```bash
smc4 doctor
python research/candidate-08/test_logic.py -v
```

## Data

`data.py` downloads only official Binance Vision USD-M monthly kline archives and their adjacent
`.CHECKSUM` files. Cached files are rehashed on every load; mismatches are deleted and downloaded
again. The run fails when OHLC ordering is invalid or missing one-minute observations exceed 0.2%.

The source interval's `close_time`, not `open_time`, is used as both Nautilus bar event and
observation time. A strategy therefore cannot inspect a bar before it is complete. Causally
confirmed pivots additionally preserve their older visual event time and later confirmation time.

Each window writes `data_manifest.json` with URL, published checksum URL, local SHA-256, byte size,
row count, timestamp unit, gaps, and first/last observable time.

## Engine

The runner uses `nautilus_trader.backtest.engine.BacktestEngine` with:

- HEDGING OMS and MARGIN account;
- contingent OUO brackets;
- adaptive high/low bar ordering;
- one-tick slippage probability 1.0;
- maker/taker fee model at the effective 6 bp rate;
- venue leverage 125 with liquidation enabled;
- no custom fill, order, account, portfolio, or PnL engine.

The instrument margin fractions are `margin_init=1.0`, `margin_maint=0.5`; with Nautilus's venue
leverage parameter this represents 1/125 initial and 0.5/125 maintenance notional margin rather than
applying leverage twice.

## Commands

```bash
# fixed three-week screen
python research/candidate-08/run.py \
  --suite screen \
  --output artifacts/candidate-08-screen

# predeclared long run; execute only after the screen promotion gate passes
python research/candidate-08/run.py \
  --suite long \
  --output artifacts/candidate-08-long
```

The GitHub workflow `.github/workflows/candidate-08.yml` runs the same commands inside the pinned
project image and preserves all evidence as an Actions artifact.

## Output

For every window:

```text
run.json
metrics.json
data_manifest.json
scenario_events.jsonl
orders.csv
fills.csv
positions.csv
account.csv
trade_intents.json
position_outcomes.json
skipped_setups.json
equity_curve.json
```

The suite root adds `suite_metrics.json` and its own `run.json`. A successful run must end with zero
open positions, zero open orders, no denied/rejected orders, and a valid causal event chain.
