# candidate-03 — ADSE-v1

**Adaptive Deleveraging State Engine** is one independent complete candidate. It does not depend on another research branch.

Current status: the frozen center passed exact aggregate-trade replay on four opened BTC development weeks. Three newly selected BTC validation weeks remain untouched. This candidate is not a project success until all three weekly gates and the subsequent long evaluation pass.

## Why the preceding candidate failed

LCPT-v1 treated a two-stage price/flow move with falling open interest as one continuation mechanism. It passed three development weeks but failed the first untouched week `2023-04-10`: 6 trades, 33.3% win rate, −0.315R mean, and −0.852% daily geometric growth.

Controlled diagnosis found no data, fee, sizing, timestamp, or order-accounting defect. The failure was logical: the same visible OI contraction can mean either efficient forced-flow propagation or high-turnover deleveraging that drifts, pulls back, and only then resumes. A single continuation entry cannot represent both.

## Structural breakthrough

ADSE measures the market state before either scenario is allowed:

```text
regime ratio at T
= median(abs(5m OI change), previous 6h)
  / median(1m ATR in price bps, previous 6h)
```

The current OI change and current minute are excluded. The ratio is therefore known before the confirmation at `T`.

- Lower ratio: price is moving efficiently relative to OI turnover. A two-stage liquidation propagation scenario is eligible.
- Higher ratio: OI is turning over heavily relative to price volatility. A trend pullback followed by spot/futures reacceleration is eligible.
- The interval `1.0 <= ratio < 1.4` is an explicit overlap band, not a brittle binary classifier. Both causal scenarios may be eligible; their confirmed events compete chronologically for the one global slot. Signal strength never changes risk or quantity.

## Scenario A — LCPT propagation

```text
5m ignition price shock
+ futures aggressor flow in shock direction
+ spot not strongly opposing
+ OI contraction
        ->
next 5m price and spot/futures flow continue
+ materially larger OI contraction
+ event has not already overextended
        ->
one completed minute without stop invalidation
        ->
first later futures aggregate trade entry
```

This scenario is allowed when `regime ratio < 1.4`.

## Scenario B — TPR deleveraging drift

```text
causal 60m directional drift
+ one 5m counter-direction pullback
+ weak/counter futures aggressor flow
        ->
next 5m price resumes
+ futures and spot aggressor flow realign
+ resumption closes through pullback open
        ->
one completed minute survives and closes in trend direction
        ->
first later futures aggregate trade entry
```

This scenario is allowed when `regime ratio >= 1.0`.

The TPR scenario is not an arbitrary rescue filter. It represents the mechanism that LCPT-v1 omitted: heavy position turnover can absorb immediate continuation while preserving a slower directional inventory unwind.

## Global state and execution

```text
IDLE
  -> scenario observation
  -> scenario confirmation
  -> ENTRY_BUFFER
      -> INVALIDATED / ENTRY_FILLED
  -> POSITION_ACTIVE
      -> STOP / TARGET / STRUCTURAL_TRAIL / TIME
  -> CLOSED
```

Across both scenario families, pending new entries plus open positions are limited to one. Exit orders that reduce or close the existing position do not consume a new-entry slot.

Quantity uses the current whole-account NAV:

```text
planned loss = current NAV * 0.03
quantity = planned loss / expected loss per unit
```

Expected loss per unit includes adverse entry-to-stop fill distance, entry and stop fees, entry and stop slippage/impact, and maximum holding-period funding. No model score, direction, instrument, or regime state changes the 3% risk fraction.

Execution assumptions:

- taker fee: 5 bps on each fill
- slippage and market impact: 1.5 bps on each fill
- funding: 1 bp per 8 hours
- exact Binance USD-M aggregate-trade event order
- mark-to-market NAV drawdown, not closed-trade drawdown only

## Frozen center

| Component | Value |
|---|---:|
| LCPT regime maximum | `1.40` |
| TPR regime minimum | `1.00` |
| regime history | previous 6 hours |
| LCPT ignition shock | `10 bp` |
| LCPT first OI drop | `1 bp` |
| LCPT continuation OI drop | `20 bp` |
| LCPT prior extension maximum | `50 bp` |
| TPR prior trend | `20–200 bp / 60m` |
| TPR pullback | `>= 5 bp` |
| TPR reacceleration | `>= 5 bp` |
| entry buffer | `1 completed minute` |
| LCPT stop / target | cascade extreme ± `0.20 ATR`; net `6R` |
| TPR stop / target | pullback extreme ± `0.10 ATR`; net `3R` |
| LCPT protection | activate `2R`, lock net `0.5R`, trail 20m |
| TPR protection | activate `1.5R`, lock net `0.5R`, trail 15m |
| maximum holds | LCPT 240m; TPR 180m |

## Exact opened-week development evidence

| BTC week | Trades | Win rate | Mean net R | Daily geometric NAV growth | MTM MDD | LCPT / TPR |
|---|---:|---:|---:|---:|---:|---:|
| 2022-03-07 | 13 | 61.54% | +1.168R | +6.145% | 8.55% | 11 / 2 |
| 2025-03-17 | 8 | 62.50% | +0.781R | +2.560% | 6.50% | 8 / 0 |
| 2022-07-18 | 15 | 53.33% | +0.575R | +3.399% | 15.17% | 14 / 1 |
| 2023-04-10 | 11 | 54.55% | +0.456R | +2.008% | 13.53% | 3 / 8 |

The fourth week was the untouched LCPT-v1 failure. It became development data only after that failure was observed and diagnosed.

Conservative adverse-first one-minute neighbor checks retained all four gates over multiple adjacent settings, including regime overlap pairs `(1.4, 1.0)`, `(1.4, 1.1)`, `(1.5, 1.0)`, and `(1.5, 1.1)`, LCPT shock `9–11 bp`, LCPT target `5–7R`, and TPR resumption `4–6 bp`. The exact center was then replayed at aggregate-trade resolution.

Machine-readable evidence is in `results/development_summary.json`.

## Newly frozen untouched validation order

The deterministic salt is:

```text
candidate-03|ADSE-v1|BTCUSDT
```

Previously opened or previously named research weeks are excluded. Candidate weeks span `2021-01-04` through `2025-12-22` and selected weeks are at least 180 days apart.

1. `2025-05-05`
2. `2022-09-19`
3. `2023-06-05`

The workflow opens week 2 only if week 1 passes, and week 3 only if week 2 passes. Long evaluation is prohibited until all three pass unchanged.

## Reproduction

The project environment is prebuilt; do not reinstall NautilusTrader.

```bash
smc4 doctor
PYTHONPATH=src:research/candidate-03 \
  python research/candidate-03/test_adse.py
python research/candidate-03/select_adse_weeks.py

python research/candidate-03/download_lcpt_bundle.py \
  --symbol BTCUSDT \
  --week-start 2025-05-05 \
  --output .research-data/candidate-03/adse/validation-1

PYTHONPATH=src:research/candidate-03 \
  python research/candidate-03/run_adse.py \
  --futures-agg .research-data/candidate-03/adse/validation-1/futures-agg/*.zip \
  --spot-agg .research-data/candidate-03/adse/validation-1/spot-agg/*.zip \
  --metrics .research-data/candidate-03/adse/validation-1/metrics/*.zip \
  --week-start 2025-05-05 \
  --label btc-adse-validation-1-2025-05-05 \
  --output artifacts/candidate-03/adse/validation-1
```

Each replay writes `run.json`, `metrics.json`, `signals.csv`, `trades.csv`, and causally validated `scenario_events.jsonl`. Manifests record SHA-256 hashes and event counts.

## Known failure conditions

- OI snapshots can be delayed or invalid; explicit zero/missing rows are excluded and break state continuity.
- A news shock or liquidation gap can fill beyond the expected stop, causing realized loss below −1R despite correct sizing.
- Public aggregate trades reveal executed aggressor flow, not L2 queue depletion, refill, or this account's actual market impact.
- The fixed 1.5 bp impact assumption can understate cost as NAV-based quantity grows relative to depth.
- The regime ratio can move through the overlap band quickly; one global slot deliberately prevents simultaneous scenario exposure but can reject a later superior event.
- Low-activity markets can produce too few independent events even when conditional expectancy remains positive.
- BTC development success does not establish transfer to ETH, SOL, or XRP. Cross-instrument testing follows only after unchanged BTC validation and long evaluation.

## Promotion rule

No partial progress is a success. Promotion requires:

1. all three newly frozen weekly gates,
2. unchanged long BTC evaluation with continuous NAV accounting,
3. unchanged causal logic on ETH, SOL, and XRP,
4. one global pending/open slot across all four instruments,
5. after-cost portfolio daily geometric growth of at least 1% with recoverable drawdown.
