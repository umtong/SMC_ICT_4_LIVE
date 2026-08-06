# Candidate-09 — Liquidity Reaction Auction Engine (LRAE)

This branch is an independent, complete SMC/ICT day-trading candidate.  It does
not assume that another research branch will supply missing logic later.

LRAE does **not** enter merely because a rolling high/low, liquidity sweep, FVG
or BOS was detected.  A prior liquidity boundary breach begins as a neutral
auction event.  The system then follows one of two causal paths:

1. **Absorption → reclaim → reversal.**  Aggressive orders cross the boundary,
   price progress fails, the old range is reclaimed, and only then is a reversal
   bracket submitted.
2. **Depletion → acceptance → held retest → continuation.**  The opposing side
   is depleted, price is accepted outside the old range, the first retest holds,
   and only then is a continuation bracket submitted toward a previously
   observed external liquidity level.

The complete-bar pattern detector is separate from the state machine and from
NautilusTrader execution/accounting.

## Frozen research contract

The weekly sample is fixed before any PnL is observed.

```text
seed: 20260806
population: every Monday from 2023-01-02 through 2025-12-22
algorithm: random.Random(seed).sample(population, 3)

w1-discovery:   2024-10-14 through 2024-10-20 UTC
w2-replication: 2024-05-13 through 2024-05-19 UTC
w3-replication: 2025-01-13 through 2025-01-19 UTC
```

`downloader.validate_frozen_selection` and a unit test independently reproduce
the selection.  Weeks cannot be replaced because their PnL is unattractive.

The base candidate is compared with exactly one core-variable ablation:
`no_flow`, which removes only the aggressor-flow polarity gate.  All state,
level, entry, stop, target, risk and cost rules remain identical.

## Mechanical SMC/ICT definitions

| Term | Candidate-09 definition |
|---|---|
| Dealing range | Highest and lowest completed prices in the prior 20 one-minute bars; the current bar is excluded. |
| Liquidity breach | Current completed bar trades at least `0.02 ATR` beyond one side of that prior range. It is directionally neutral. |
| Sweep/reclaim | The breach is followed within three completed bars by a close back inside the prior range, visible rejection, sufficient activity and opposite-flow response. |
| BOS/acceptance | A close at least `0.10 ATR` outside the prior range with displacement, close-location, activity and flow confirmation. |
| CHoCH | Not a single candle label. It is the state transition from breached auction to reclaimed range, which reverses the active price-response regime. |
| Displacement | Range expansion relative to prior ATR, directional close location and aggressor-flow alignment. |
| FVG/order block | Not standalone signals in this version. Their execution role is represented by the first held retest after acceptance. |
| External liquidity | A confirmed causal pivot or prior 360-bar extreme beyond the current price, observed before entry. |
| Premium/discount | Implicit in the entry’s location relative to the frozen internal range and its opposite liquidity target; never used alone. |
| Session timing | No hard-coded kill zone. Continuous 24/7 BTC observations are used so time-of-day cannot mask weak scenario logic. |

A bar that breaches both range sides is not traded because one-minute OHLC data
cannot resolve the actual intrabar sequence.

## Causal timing

Binance Vision UM futures one-minute klines are normalized so that
`ts_init == close_time_ns`.  The strategy sees OHLC, volume and taker-buy volume
only after that minute has completed.  Confirmed pivots retain their historical
event time but are added to the available liquidity map only after the right-side
confirmation bars have closed.

NautilusTrader processes the bar’s synthetic OHLC path before `on_bar`.  A
market entry submitted from `on_bar` therefore executes against the completed
bar close.  Resting stop and target orders are handled by NautilusTrader’s
matching engine with adaptive high/low ordering.

## Risk and cost contract

For every accepted plan:

```text
risk budget = current full-account NAV × 3%

planned loss per BTC
  = |entry - stop|
  + effective entry cost
  + effective stop cost

quantity
  = floor(risk budget / planned loss per BTC, exchange size increment)
```

No model-score multiplier, separate nominal cap or candidate-specific leverage
cap is added.  The simulated venue uses the product’s 20x margin setting.  A
single net position/order list is allowed.

The effective rate is `6.5 bps per side`:

```text
5.0 bps assumed exchange taker fee
1.5 bps spread, slippage and market-impact allowance
```

Both maker and taker fees are set to this rate.  This prevents a bar backtest
from receiving an unearned maker advantage.  All reported NAV and PnL are
NautilusTrader account results after these costs.

## Evaluation

The 1% threshold is a final gate, not a parameter-search objective.  The frozen
weekly gate requires:

- cost-after pooled geometric daily NAV growth at least 1%;
- each individual frozen week at least 1% geometric daily growth;
- at least five closed trades in each week;
- no week with maximum drawdown above 25%;
- no week where the three largest winners exceed 50% of gross positive PnL;
- flat end state and no rejected orders.

Failure is not summarized as a single binary.  `aggregate.json` retains each
week, scenario branch, state transition, trade concentration and the one-variable
ablation.  `decision.json` separates implementation error from logic failure and
records the largest observed performance factor plus any component that still
worked.

## Reproduction

Use the prebuilt repository environment.  Do not install or replace
NautilusTrader.

```bash
smc4 doctor

python -m unittest discover \
  -s research/candidate-09/tests \
  -p 'test_*.py' -v

PYTHONPATH=src python research/candidate-09/run_research.py \
  --config research/candidate-09/config.json \
  --output artifacts/candidate-09
```

The GitHub workflow `.github/workflows/candidate-09.yml` runs the same commands
inside the repository’s pinned image.

Expected evidence tree:

```text
artifacts/candidate-09/
├── run.json
├── data_manifest.json
├── aggregate.json
├── decision.json
├── SUMMARY.md
└── runs/
    ├── base/<week>/
    │   ├── run.json
    │   ├── metrics.json
    │   ├── events.jsonl
    │   ├── trades.json
    │   ├── fills.csv
    │   ├── positions.csv
    │   ├── account.csv
    │   └── equity.csv
    └── no_flow/<week>/...
```

## Source map

- `lrae.py`: causal pattern detector, liquidity map, state machine and trade plans.
- `strategy.py`: NautilusTrader strategy, one-position enforcement and 3% NAV sizing.
- `backtest.py`: NautilusTrader venue/instrument/data setup and reports.
- `downloader.py`: deterministic Binance Vision acquisition and timestamp validation.
- `run_research.py`: frozen-week orchestration, ablation, aggregation and diagnosis.
- `config.json`: all pre-PnL choices and success gates.
- `tests/`: causal timing, frozen selection, ambiguous-bar rejection and risk-budget tests.

Large market data and raw run artifacts are not committed.  Their SHA-256
manifest and compact verified result summary are committed after execution.
