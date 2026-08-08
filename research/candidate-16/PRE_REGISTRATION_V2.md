# Candidate 16 v2 pre-registration

Frozen before the first Candidate 16 v2 NautilusTrader result is inspected.

## Why v2 exists

Candidate 16 v1 classified 172 failed auctions and submitted 167 entries in one
week. The REJECTION branch lost 96,448.66 USDT. Its code recorded displayed depth
response but did not use it, and it entered on the same completed bar that first
labeled the auction a failure. Thirty-two entries also filled through their
planned stop and remained exposed after Nautilus rejected the protective child.

v2 changes these structural defects. It does not tune v1 numeric thresholds to
its outcome.

## Reused implementation

- Candidate 05 checksum-verified Binance Vision data and feature preparation;
- Candidate 05 NautilusTrader `BacktestNode`, account, fill, fee, margin, and NAV path;
- Candidate 16 v1 parent-liquidity identity and effort/result first pass;
- active confirmed-swing and completed-session liquidity pools;
- current-NAV 3% planned-loss sizing;
- one global pending entry or position;
- unconsumed natural liquidity objectives after costs.

## Single strategy change

A v1 failed-auction label is not tradable by itself.

```text
parent external-liquidity attack
→ high-effort / low-persistent-progress reclaim
→ displayed book supports the reversal side at least once
→ displayed liquidity defending the attacked boundary replenishes at least once
→ FAILURE_FROZEN; no order
→ strictly later completed bar:
     price closes beyond the failure-bar extreme
     AND aggressor flow is in the reversal direction
     AND 60-second price return is in the reversal direction
     AND displayed book imbalance supports the reversal direction
     AND displayed liquidity ahead of the reversal withdraws
→ reversal entry
```

Before the later initiative, any re-access of the full parent extreme invalidates
the scenario. If no complete initiative appears within three later completed
bars, the scenario expires unresolved.

True acceptance remains separate. The v1 outside-residence/efficient-progress
state must additionally have:

```text
displayed book imbalance in the attack direction
AND displayed liquidity ahead of the attack withdraws at least once
```

Only then may the inherited first defended retest arm continuation.

All v2 displayed-liquidity tests use categorical sign and temporal ordering. No
magnitude was selected from v1 PnL.

## Mandatory execution correction

If an actual next-bar market fill has already crossed the planned stop and
Nautilus rejects the protective `STOP_MARKET` child as marketable, v2 cancels the
remaining bracket and immediately requests a market flatten. It may never remain
open until the ordinary maximum-hold exit.

Any order rejection still fails the integrity gate; the correction limits damage
rather than converting a rejected order into valid success evidence.

## Deterministic untouched screening interval

The interval was selected without inspecting v2 output.

1. Enumerate every Monday from `2022-01-03` through `2025-12-29` inclusive
   (209 candidates).
2. Hash the exact UTF-8 seed:

   ```text
   candidate16-v2-displayed-liquidity|91317f522546afd837e330a2bde0f9c05e81b068|independent-week
   ```

3. Interpret SHA-256 as an unsigned integer and select `hash mod 209`.
4. The index is `141`, selecting Monday `2024-09-16`.

Frozen data windows:

- build/warm-up: `2024-09-13` through `2024-09-22` UTC;
- evaluation: **`2024-09-16` through `2024-09-22` UTC**;
- one continuous 100,000 USDT account; no daily reset.

After this run is inspected, the interval becomes development data.

## Screen gate

The unchanged screen requires:

- geometric daily growth at least 1%;
- at least 7 trades and 4 wins;
- win rate at least 40%;
- at least 4 active days;
- maximum drawdown at most 20%;
- largest winner share at most 55%;
- positive final NAV;
- no liquidation or order rejection;
- at most one entry intent and one position;
- Nautilus-generated orders and positions.

The gate is a rejection screen, not a tuning objective and not long-run success
evidence.

## Decision rules after the screen

- Few or no frozen failures: the coarse public-depth data cannot express the
  proposed defense state; do not loosen signs. Move to bookTicker/depth-event data.
- Many frozen failures but few initiatives: reclaim is common but real opposite
  initiative is rare; preserve no-trade.
- Many entries with poor expectancy: displayed bookDepth aggregates are not a
  sufficient proxy for replenishment/resiliency; retire this data representation.
- Positive first week: advance unchanged to additional untouched intervals before
  any continuous long-horizon claim.
