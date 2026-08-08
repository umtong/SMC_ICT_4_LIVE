# Candidate 15 V6 — Residual-Laggard Delivery

Candidate 15 is continuing after V5 because V5 exposed a structural ownership
error rather than merely a weak threshold.

- V1 showed that a resolved local auction state cannot be stored forever.
- V2 made the state a short causal decision lease and exposed a stop moved inside
  the original sweep invalidation.
- V3 enforced scenario-terminal sweep invalidation and exposed a faulty static
  cross-market role decision.
- V4 created abundant repeated-common-flow continuation activity, but its
  five-minute impulse was normalized by one-minute ATR and repetition did not
  require price progress.
- V5 corrected the timeframe and required actual common-market response. It
  still lost because every market was allowed to consume a broad state that it
  may already have created.

The rejected evidence is preserved in `V1_FAILURE.md` through `V5_FAILURE.md`.

## V5 causal attribution

The persisted V5 lifecycle, submitted plans, opening order identities and
Nautilus positions were joined without inventing a new simulator. Of 61 filled
trades:

| Relation to current confirming response | Trades | Wins | Realized PnL |
|---|---:|---:|---:|
| Market already in `accepted_symbols` | 53 | 5 | `-107,001.215528` USDT |
| Sole market excluded from a three-market response | 8 | 3 | `+42,826.148130` USDT |

All three excluded-market wins occurred in E01, so this is concentrated exposed
development evidence. It does not establish alpha. It does identify a distinct
mechanism to test: information already delivered versus delayed delivery.

## V6 hypothesis

The response state is not broad permission. It has market ownership.

```text
first completed 5m common-flow impulse
  standardized by prior completed 5m ATR
                    ↓
second same-direction event within 4h
                    ↓
>=3 common markets + positive median signed progress
+ majority advances + majority holds first origins
                    ↓
response-qualified initiative
                    ↓
accepted_symbols == exactly three markets?
       ↙                               ↘
      no                               yes
NO TRADE: no residual         sole excluded market is
information receiver          residual information receiver
                                        ↓
                       excluded market later completes
                       fresh post-activation 5m MSS
                       + displacement + strict FVG
                                        ↓
                         passive CE retracement
                                        ↓
                   protected same-leg invalidation
                   + live external 4H/day objective
```

The three accepted markets are terminal no-trade for this family. They supplied
the state evidence and cannot use the same common move again as independent
continuation permission. A four-market response has no excluded receiver and is
also no-trade.

## What V6 changes

Only portfolio ownership changes:

- the V5 response-qualified state detector is unchanged;
- all four continuation engines remain observed so rejected plans are visible;
- only the sole market absent from exactly three accepted markets may compete;
- every accepted-market continuation receives an explicit terminal
  `C15_V6_NOT_RESIDUAL_LAGGARD` transition;
- the approved plan records `candidate15_v6_route`, and aggregation fails the
  route audit if any submitted market belongs to `accepted_symbols`.

## What V6 does not change

- five-minute MSS, displacement and strict three-candle FVG entry geometry;
- post-only consequent-encroachment entry and causal GTD expiry;
- protected swing/opposing-bar invalidation;
- next live completed-4H or previous-day external objective;
- realistic maker/taker costs and inherited fill assumptions;
- current whole-account NAV × 3% planned-loss sizing;
- one pending entry or open position across all four markets;
- NautilusTrader ownership of clocks, orders, fills, margin, positions and NAV;
- the predeclared development gate.

No custom backtester, portfolio simulator, score-based risk multiplier, leverage
cap, fallback target or post-hoc result filter is added.

## Development protocol

E01-E06 are the same exposed diagnostic weeks used by V4 and V5. They are reused
to isolate the ownership change and can never support a success claim. The
unchanged gate requires sufficient activity and interval breadth, positive
costed growth, adequate win rate and payoff, bounded drawdown, non-concentrated
growth and complete execution safety.

```bash
for interval in E01 E02 E03 E04 E05 E06; do
  bash research/candidate-15/run_week.sh "$interval"
done
python research/candidate-15/aggregate_v6.py
```

Only a promising exposed result permits a frozen, newly predeclared confirmation
screen. Project success still requires one frozen continuous Nautilus account,
realistic costs, no liquidation or unrecoverable NAV damage, and the project's
minimum long-run costed growth standard.
