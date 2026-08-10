# Candidate 57 — frozen multi-regime replication of delayed jump price confirmation

## Rule under replication

The 2026-07-29 through 2026-08-09 untouched comparison produced a clear but
small result:

- immediate peer-taker-conditional jump reversal: 9 trades, PF 0.646 and
  -0.306% geometric growth per day;
- wait at least two completed five-minute bars and require a close through the
  terminal jump-candle extreme: 3 trades, 2 wins/1 loss, PF 3.893 and +0.364%
  geometric growth per day;
- adding the imported 1% open-interest stabilization condition changed no
  trade, so OI is not part of this replication.

The positive delayed result was concentrated in three XRP trades. It is not
sufficient evidence for promotion. This document freezes the exact price-only
rule, without parameter changes, across three disjoint regimes which were not
used to design it.

## Replication intervals

1. **2025-02-03 through 2025-02-16 UTC**;
2. **2025-07-07 through 2025-07-20 UTC**;
3. **2026-01-12 through 2026-01-25 UTC**.

Each interval is a separate continuous four-symbol, one-slot account. Results
must not be concatenated into a fictitious account. The pooled evidence is used
only to diagnose sign stability, trade concentration and opportunity density.

## Frozen cells in every interval

| cell | entry state |
|---|---|
| `immediate_control` | immediate entry after source-boundary arbitration |
| `two_bar_price_confirmation` | wait at least ten completed minutes; confirm only on a completed five-minute close through the terminal jump-candle extreme in the reversal direction |

Delayed candidates expire after 15 completed minutes. Structural invalidation
includes any post-jump extension, and the original 240-minute source-event clock
is not restarted.

## Unchanged contract

- completed four-hour return with absolute prior-only z-score at least 2.0;
- 18 prior completed four-hour returns for volatility;
- proposed side opposite the completed impulse;
- peer-taker conditional arbitration:
  - at least three of four peer taker ratios aligned -> source max absolute z;
  - otherwise -> least absolute qualifying z;
- strict as-of Binance metrics, all four peers, maximum age ten minutes;
- both reversal directions;
- whole-impulse structural stop;
- transient protection armed at +0.4R and escaped at +1.0R;
- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
- one global pending entry or open position;
- current-NAV 3% planned-loss sizing;
- realistic project costs, funding safety and NautilusTrader matching;
- simultaneous symbols at one source four-hour boundary are one causal event.

## Interpretation

The delayed rule is a viable specialist only if it has positive cost-after
expectancy in at least two of the three accounts, is not dependent on one symbol
or one trade, and improves the majority of accounts relative to the immediate
control. The 1% project growth target is not required of this low-frequency
specialist alone, but its independent opportunity density and slot occupancy
must justify later integration.

If the sign is unstable, stop treating immediate four-hour cascade reversal as
an active alpha family. Preserve its source detector and confirmation code only
as reusable components for a different scenario.
