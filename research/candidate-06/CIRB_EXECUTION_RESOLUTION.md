# Candidate 06 CIRB — parent-frozen five-second response-resolution ablation

## Research question

The frozen CIRB result separated a structural signal-frequency problem from an
execution-geometry problem. Week 2 contained completed CIRB responses, but every
armed entry was rejected because its cost-after-entry reward/risk fell below the
unchanged `0.60` floor. CIRB already submits on the completed one-minute signal
close; therefore this experiment does **not** pretend that an additional minute
of order latency exists.

The single changed question is narrower:

```text
completed five-minute OI/crowding parent event
        -> unchanged DISCHARGE or COUNTER_INVENTORY branch
        -> compare response confirmation on:
             A. completed one-minute bars (authoritative baseline)
             B. completed five-second aggregate-trade bars
        -> unchanged structural stop/objective family
        -> unchanged cost-after-entry geometry gate
        -> native NautilusTrader order, fill, position and NAV path
```

This is a response-resolution ablation, not a threshold search. A five-second
bar may never create, delete, relabel or reverse the already-frozen parent event.

## Causal boundary

The one-minute CIRB Nautilus run is executed first. Its event ledger freezes the
population of completed parent shocks actually observable under the original
single-slot strategy. For each parent event, the five-second state machine starts
strictly **after** the parent event timestamp. The event bar itself cannot trade.

Five-second data are checksum-verified Binance USD-M `aggTrades`. Every trade is
bucketed by exchange event time. A bar is visible only at its exact interval end.
A no-trade bucket carries the last already-observed price and zero flow; no future
trade is backfilled.

## Unchanged market logic

- Parent shock: prior-only extreme completed OI contraction, directional
  five-minute price move and aligned completed taker flow.
- Branch: sign of completed all-account composition change relative to the shock.
- `DISCHARGE`: later opposite reclaim may reverse; later OI contraction plus
  price discovery may continue.
- `COUNTER_INVENTORY`: reversal remains forbidden; continuation still requires a
  later completed OI rebuild, persistence of opposing composition and renewed
  shock-direction price/flow extension.
- Reversal objective candidates remain impulse origin, pre-shock dealing-range
  equilibrium and pre-shock fast-range liquidity.
- Continuation objective remains the unchanged deleveraging-range projection.
- Stop buffer, response horizons and invalidation horizons preserve the original
  wall-clock duration rather than the original number of bars.

## Unchanged execution and risk

- one native NautilusTrader margin account;
- one pending/open global slot;
- current whole-account NAV sizing;
- planned loss `3%` per approved trade;
- effective fee `0.0007` per fill;
- one adverse tick on entry and stop;
- limit-touch probability `0.65` where a limit is used;
- minimum structural R `0.75`;
- minimum cost-after-entry R `0.60`;
- maximum holding time `45` wall-clock minutes;
- no nominal cap, leverage cap, score multiplier or fitted session filter.

## Predeclared variants and gate order

1. `cirb_full_1m_baseline` — exact original runner, used for identity and
   performance reproduction.
2. `cirb_full_5s_response_resolution` — both causal branches, selectable.
3. `cirb_discharge_only_5s_attribution` — branch attribution only, never
   selectable in this experiment.

Week 2 is evaluated first because it already proved that the one-minute system
had five armed responses but no executable cost-after-entry geometry. If neither
five-second variant produces at least one actually closed, positive-cost trade
from that rejected population while parent identity remains exact, the
resolution hypothesis is rejected without opening the other two weeks.

If Week 2 shows structural rescue, Weeks 1 and 3 are run unchanged. Promotion
requires all three weekly gates, zero semantic drift, exact parent identities and
combined cost-after-fee daily geometric NAV growth of at least `1%`.

## Required diagnostics

- `parent_event_identity_hash`;
- `parent_signal_identity_hash`;
- `parent_signals_armed`;
- `child_5s_candidates`;
- `geometry_rescued_submissions`;
- `rescued_by_5s_closed_trades`;
- `still_rr_eroded`;
- `entry_delay_seconds`;
- `entry_price_improvement_bps`;
- `semantic_drift_count`.

A profitable result is not accepted if parent identity changes. A failed result
does not authorize lowering the RR floor, changing the crowding sign split,
retargeting the frozen weeks or fitting five-second flow thresholds.
