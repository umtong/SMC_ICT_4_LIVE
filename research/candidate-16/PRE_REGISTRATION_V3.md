# Candidate 16 v3 pre-registration

Frozen before the first Candidate 16 v3 NautilusTrader result is inspected.

## Why v3 exists

Candidate 16 v2 repaired the temporal state chain and execution protection, but
its Binance public `bookDepth` inputs were only the last notional snapshot in
±1%/±2% distance bands for each minute. On an untouched BTC week, v2 reduced 167
v1 entries to 21 yet produced 7 wins, 14 losses, `-14.42%` NAV, and a 35% win
rate in the 20-trade rejection branch. The representation did not identify
actual queue replenishment or withdrawal at the traded boundary.

v3 changes the displayed-liquidity observation only. It does not change any
v1/v2 numeric strategy, entry, stop, target, holding, cost, or risk parameter.

## Reused implementation

- Candidate 05 checksum-verified completed-minute klines and aggregate trades;
- Candidate 05 NautilusTrader `BacktestNode`, margin account, fees, latency,
  liquidation, orders, positions, and continuous NAV;
- Candidate 03 checksum-verified Binance Vision `bookTicker` ingestion and
  transaction/observation timestamp normalization;
- Candidate 16 v2 parent identity, failure-without-order, later initiative,
  unconsumed objective, actual-fill fail-close, and one global account slot.

## Top-of-book observation

For every completed UTC minute, the exact best-bid/best-ask event sequence is
processed in original observed order. The feature builder records:

- first/end midpoint and midpoint path;
- first/end/min/max spread;
- end best-quote imbalance;
- same-price best-quote size additions and removals;
- best-price improvements and retreats;
- whether the final best-price episode was depleted and then refilled with the
  replenished size still present at minute completion.

The signed queue response is categorical:

```text
+1 DEFENSE
    spread at minute completion is no wider than at minute start
    AND either:
        the final best-price queue was depleted, refilled, and retained
        OR the best price improved toward the opposite side

-1 WITHDRAWAL
    best price retreated away from the opposite side
    AND same-price removals exceeded additions

 0 DISAGREEMENT / NO STATE
```

Ask response is used for an upward attack or long initiative; bid response is the
mirror for a downward attack or short initiative.

## Failed-auction sequence

The v2 sequence remains frozen:

```text
external-liquidity attack
→ high effort / low persistent progress / completed boundary reclaim
→ best quote ahead of the attack shows DEFENSE
→ end best-quote imbalance supports the prospective reversal
→ FAILURE_FROZEN; no order
→ strictly later completed minute:
     midpoint return is in the reversal direction
     AND aggressor flow is in the reversal direction
     AND end best-quote imbalance supports the reversal
     AND liquidity ahead of the reversal shows WITHDRAWAL
     AND price closes beyond the failure-bar extreme
→ reversal entry
```

Any re-access of the full parent extreme before the later initiative invalidates
the scenario. No complete initiative within three later minutes expires it.

## True-acceptance sequence

```text
outside residence with efficient progress
→ end best-quote imbalance supports the attack
→ best quote ahead shows WITHDRAWAL
→ inherited first defended retest
→ continuation entry
```

Static imbalance alone, same-minute failure and entry, or disagreement between
price, trade flow, and queue response cannot trade.

## Fixed account and execution contract

- one continuous 100,000 USDT margin account;
- current whole-account NAV × 3% planned loss per entry;
- one pending entry or open position globally;
- all-in 7.5 bp fee assumption on each side plus 2.5 bp adverse slippage on each
  side in planned-loss and structural-R calculations;
- NautilusTrader owns orders, fills, contingent protection, margin,
  liquidation, positions, and NAV;
- if an actual market fill has already crossed its planned stop, remaining
  children are canceled and the residual position is immediately fail-closed.

## Deterministic untouched screening interval

The period is selected without inspecting v3 output.

1. Enumerate every Monday from `2022-01-03` through `2025-12-29` inclusive
   (209 candidates).
2. Hash the exact UTF-8 seed:

   ```text
   candidate16-v3-top-of-book-resiliency|0d43da0256af7d4d2a1aa81dcdb98fec8f625cda|independent-week
   ```

3. Interpret SHA-256 as an unsigned integer and select `hash mod 209`.
4. SHA-256 is
   `5bcc531832c121c21e26750e8bf72ec0ca2b04dd500b339b9583b112cfb56ebe`.
5. The index is `49`, selecting Monday `2022-12-12`.

Frozen windows:

- build/warm-up: `2022-12-09` through `2022-12-18` UTC;
- evaluation: **`2022-12-12` through `2022-12-18` UTC**;
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

This screen rejects weak candidates. Passing one week is not a long-run success
claim.

## Decision rules after the first screen

- No usable bookTicker coverage: implementation/data failure; fix provenance or
  parsing without changing alpha.
- Parent failures rarely show defense: preserve no-trade; do not loosen queue
  signs.
- Defense occurs but later initiative is rare: the completed state is selective;
  preserve it and inspect occurrence before considering a larger opportunity set.
- Many initiatives with poor expectancy: best-quote snapshots still do not
  identify durable inventory ownership; retire this candidate family rather than
  tune v1/v2 thresholds.
- Positive screen: advance unchanged to additional deterministic untouched
  intervals, then one continuous longer account before any success claim.
