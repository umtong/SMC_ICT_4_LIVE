# Candidate 16 v4 pre-registration

Frozen before the first Candidate 16 v4 NautilusTrader result is inspected.

## Why v4 exists

Candidate 16 v1 proved that high effort, low progress, and a reclaim are not a
tradable failed auction by themselves. Candidate 16 v2 repaired temporal state
ownership but its ±1% minute depth-band snapshot still produced 7 wins and 14
losses on an untouched week. Candidate 16 v3 attempted actual event-time queue
resiliency, but the pre-registered 2022 interval had no public Binance
`bookTicker` archive and therefore produced no alpha result.

External and project investigation found a stable existing solution for the
data that actually exists: a one-minute BTCUSDT perpetual L1 Parquet built by
streaming all available official monthly Binance `bookTicker` archives. v4 uses
that artifact without pretending it contains L3 order identities or same-price
queue reconstruction.

## Reused implementation

- Candidate 05 checksum-verified completed-minute bars and aggregate trades;
- Candidate 05 NautilusTrader `BacktestNode`, fees, latency, margin,
  liquidation, orders, positions, and continuous NAV;
- Candidate 16 v2 one-parent identity, failure-without-order, later initiative,
  unconsumed objective, actual-fill fail-close, and one global account slot;
- immutable public L1 artifact:
  - dataset commit `2c8dce40261855c7b57113f5a157bbeb82280bb8`;
  - Parquet SHA-256
    `274eb8e87c7d7185a0162271144b30a0e387ae496fe657c6af83833448f08624`;
  - size `28,423,067` bytes;
  - rows `460,265`;
  - coverage `2023-05-16 11:49` through `2024-03-31 23:59` UTC.

## What the L1 artifact supports

Each completed minute contains:

- average and closing spread;
- closing bid/ask quantity;
- average and closing best-quote imbalance;
- closing microprice and signed microprice premium;
- quote update count.

TWAP describes pressure over the whole minute; close describes the latest
completed state. Their sign relation can distinguish pressure persistence,
pressure reversal, and disagreement. It cannot prove individual add/cancel
ownership or hidden liquidity.

## Single strategy change

No v1/v2 numeric strategy, entry, stop, target, holding, cost, or risk parameter
changes. Only v2's coarse displayed-depth state is replaced.

### Failed auction

For parent attack direction `d`:

```text
high effort / low persistent progress / completed reclaim
AND d × TWAP imbalance > 0
AND -d × closing imbalance > 0
AND -d × closing microprice premium > 0
AND closing spread <= average spread
→ PRESSURE_FLIPPED
→ FAILURE_FROZEN; no order
```

The average minute therefore supported the attack, but the completed state
transferred to the prospective reversal side without ending in wider spread.

A strictly later minute must still provide all of:

```text
close beyond the failure-bar extreme
AND reversal-side aggregate aggressor flow
AND reversal-side one-minute price return
AND reversal-side TWAP imbalance
AND reversal-side closing imbalance
AND reversal-side closing microprice premium
AND closing spread <= average spread
```

Only then may the reversal enter. Re-access of the full parent extreme before
that initiative invalidates the scenario. No complete initiative within three
later minutes expires it.

### True acceptance

For attack direction `d`:

```text
d × TWAP imbalance > 0
AND d × closing imbalance > 0
AND d × closing microprice premium > 0
AND closing spread <= average spread
→ PRESSURE_PERSISTED
```

The inherited outside-residence state and first defended retest must also be
complete before continuation may enter.

Any sign disagreement, missing minute, zero update activity, or wider closing
spread is `UNRESOLVED / NO TRADE`.

## Fixed account and execution contract

- one continuous 100,000 USDT margin account;
- current whole-account NAV × 3% planned loss per entry;
- one pending entry or open position globally;
- all-in 7.5 bp fee assumption per side plus 2.5 bp adverse slippage per side in
  planned-loss and structural-R calculations;
- NautilusTrader owns orders, fills, contingent protection, margin,
  liquidation, positions, and NAV;
- if an actual market fill has already crossed the planned stop, remaining
  children are canceled and the residual position is immediately fail-closed.

## Deterministic untouched screening interval

The available dataset constrains the possible evidence set. To prevent selecting
an easier month after seeing output:

1. Enumerate Mondays from `2023-05-22` through `2024-03-18`.
2. Keep only weeks whose Friday warm-up start, Monday evaluation start, and
   following Sunday build end all lie in the same calendar month. This leaves 32
   candidates and avoids month-boundary source ambiguity.
3. Hash the exact UTF-8 seed:

   ```text
   candidate16-v4-l1-pressure-persistence|d6da25d44168a67a630e82093bffaec146845578|coverage-constrained-week
   ```

4. SHA-256 is
   `fc6f6286693f25e184a7283703cf41432c80af5d200a32dace24e3dc12737ef2`.
5. `hash mod 32 = 18`, selecting Monday `2023-11-20`.

Frozen windows:

- build/warm-up: `2023-11-17` through `2023-11-26` UTC;
- evaluation: **`2023-11-20` through `2023-11-26` UTC**;
- one continuous account; no daily or weekly reset.

After inspection, this interval becomes development data.

## Unchanged rejection screen

- geometric daily growth at least 1%;
- at least 7 trades and 4 wins;
- win rate at least 40%;
- at least 4 active days;
- maximum drawdown at most 20%;
- largest winner share at most 55%;
- positive final NAV;
- no liquidation or order rejection;
- at most one simultaneous entry intent and one open position;
- Nautilus-generated orders and positions.

Passing one week is not a long-run success claim.

## Decision rules after the screen

- Missing or checksum-mismatched Parquet: data/provenance failure; fix only that.
- Very few pressure flips or persistence states: preserve no-trade; do not loosen
  signs.
- Many states but few later initiatives: pressure transition is selective;
  preserve the state and diagnose opportunity coverage.
- Many entries with poor expectancy: minute L1 pressure still does not identify
  durable inventory ownership; retire Candidate 16's price/aggressor/L1 family
  instead of tuning inherited thresholds.
- Positive screen: advance unchanged to additional deterministic untouched weeks
  within the frozen coverage, then one continuous multi-month account before any
  success claim.
