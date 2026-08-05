# Candidate 04 — Causal Liquidity Transition Graph

Candidate 04 is an independent BTC-first SMC/ICT day-trading candidate. It does not treat a sweep, FVG, BOS, or displacement as a stand-alone entry pattern. It models an auction as a causal transition:

```text
past-only liquidity pool
  -> liquidity sweep
  -> failed auction (absorption/reclaim) OR accepted auction (continuation)
  -> causal confirmation
  -> next-bar executable entry
  -> opposing liquidity target OR structural invalidation
```

The first screening instrument is `BTCUSDT` Binance USD-M perpetual. The same state logic, not BTC-specific absolute thresholds, is intended for later ETH/SOL/XRP transfer only after BTC survives the staged tests.

## Research hypothesis

A recent rolling extreme is a machine-observable proxy for resting liquidity only after it has existed for a minimum age and has been approached/touched. A violation of that pool then has two economically different outcomes:

1. **Sweep–absorption reversal**: aggressive flow crosses the pool, price reclaims it, and a later displacement closes beyond the sweep bar. This is interpreted as failed continuation and inventory transfer. The next opposing contextual pool is the target.
2. **Sweep–acceptance continuation**: aggressive flow crosses the pool, price closes outside with directional body/close location, and a later bar holds outside. This is interpreted as accepted repricing. A fixed causal expansion multiple is the target because no already-known opposing pool exists beyond the break.

This distinction follows market-microstructure evidence that short-horizon price changes depend on order-flow imbalance and available depth, while persistent order flow can represent an unfinished parent order. The implementation uses Binance kline taker-buy volume as a coarse executable-data proxy; it does not claim that 1-minute bars expose the full order book.

Primary references:

- Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*.
- Donier & Bouchaud, *Why do markets crash? Bitcoin data offers unprecedented insights* and related meta-order/order-flow persistence literature.
- Binance public-data repository and USD-M daily kline archives.
- NautilusTrader backtesting and Binance integration documentation.

## Causal safeguards

- Every rolling pool, ATR, volume baseline, and context range is shifted by one bar.
- A sweep/acceptance is known only at that bar's close.
- Confirmation is known only at its close.
- Entry is filled at the following bar open with adverse slippage.
- A newly entered position may stop or target in its entry bar; there is no one-bar immunity.
- If stop and target are both touched in the same 1-minute bar, stop wins.
- Scenario events persist both market event time and algorithm observation time.
- Only one pending entry or one position can exist across the candidate.

## Risk and execution contract

Quantity uses the entire current strategy NAV and the project risk equation. Planned loss is capped at 3% of NAV and includes:

- entry-to-adverse-stop fill distance;
- entry and stop taker fees;
- adverse entry and stop slippage;
- one adverse funding event in the planning denominator.

Realized funding is charged only when an open position crosses 00:00, 08:00, or 16:00 UTC. There is no arbitrary notional cap, leverage cap, or model-score risk multiplier.

The fast research simulator exists only to reject weak scenario logic efficiently. Before promotion, surviving logic must also be replayed through NautilusTrader's order/account/portfolio path; the repository foundation already provides that engine and is not reimplemented here.

## Frozen staged evaluation

The week candidates were drawn before seeing results using `random.Random(4004)` over seven-day windows from 2023-01-01 through 2025-12-25, with at least 28 days between starts:

| Stage | UTC dates |
|---|---|
| week-1 | 2025-09-01 through 2025-09-07 |
| week-2 | 2024-12-27 through 2025-01-02 |
| week-3 | 2025-10-20 through 2025-10-26 |

The pipeline stops at the first failed week. A week passes only when all configured checks pass: after-cost geometric daily growth at least 1%, at least seven trades, activity on at least four days, drawdown at most 20%, no single winner above 55% of gross profit, positive NAV, and the one-entry/position invariant.

These gates are candidate-selection requirements, not parameter-search objectives.

## Reproduction

In the prebuilt project Codespace/Dev Container:

```bash
smc4 doctor
python -m unittest discover \
  -s research/candidate-04/tests \
  -p 'test_*.py' -v

python research/candidate-04/candidate.py pipeline \
  --config research/candidate-04/config.json \
  --output artifacts/candidate-04 \
  --data-root .cache/binance-candidate-04 \
  --max-weeks 1
```

When week-1 passes with the frozen configuration, run without `--max-weeks` to allow the sequential week-2/week-3 gates.

Each completed week writes:

- `metrics.json` — NAV, growth, drawdown, trade and scenario diagnostics;
- `trades.csv` and `nav.csv`;
- `events.jsonl` — causal state transitions and failure reasons;
- `data_manifest.json` — downloaded archive and checksum hashes;
- `run.json` — code/config/runtime provenance.

`pipeline_summary.json` records where staged evaluation stopped.

## Known failure conditions fixed before screening

- A synthetic fixture initially did not penetrate/reclaim the pool deeply enough to be a valid failed auction. The fixture, not the detector, was corrected.
- Planned risk initially omitted expected funding. Quantity sizing now reserves one adverse funding event.
- Entry processing initially risked granting a newly entered position implicit one-bar immunity. Entry now occurs before the bar's stop/target evaluation.

## Current status

Implementation and causal unit tests are complete. The GitHub Actions evidence for the frozen first week is the authoritative screening result; this section is updated only from a reproducible artifact, not from an ad-hoc local run.
