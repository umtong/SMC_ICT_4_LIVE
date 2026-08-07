# Candidate 10 — Autonomous round 2 result

Status: **project target not achieved; no success claim**.

This round replaced post-hoc FAR management with explicit causal trading states.
All promoted comparisons used the frozen Candidate 11 detector at commit
`f10517d4ccd2a1a8fbd4d31091cbe0e7c3655327`, NautilusTrader 1.230.0 for clocks,
orders, fills, fees, margin, positions and account NAV, current all-cost NAV for
the fixed 3% maximum-loss budget, the v27 size-dependent impact model, and one
global pending-entry/position slot across BTC, ETH, SOL and XRP.

No result below is a custom-vectorized PnL simulation.  Every reported NAV is a
Nautilus account result with the additional causal impact ledger debited at real
fills.  There were no risk-budget violations, no liquidation detections and no
global-slot overlaps in the completed v33--v36 experiments.

## Research question

Round 1 established that many failed-auction trades which eventually stopped out
first delivered to the midpoint of the already-completed source dealing range.
Round 2 therefore tested whether source equilibrium should be:

1. the primary economic objective rather than a later management checkpoint;
2. paired with a structurally different invalidation;
3. reached from a different causal entry state;
4. confirmed by a second post-retest displacement rather than by the first
   failed-auction displacement alone.

The detector remained separate from the trading scenario.  Session liquidity
formation, sweep identity, reclaim/MSS/displacement, independent external draw,
market leadership, costs, risk sizing and arbitration were frozen while each
trading-state variable was ablated independently.

## v33 — source equilibrium as the primary trade

### Contract

A 2x2 exact ablation independently varied:

- target: original independent external draw vs source dealing-range midpoint;
- invalidation: original raid extreme vs the complete confirmation-displacement
  zone.

The original external target was retained only as metadata for a future,
separately funded runner.  The primary trade and runner were not conflated.

### Evidence

- Controlled week `2023-04-11`:
  - external draw + raid stop: 2 losses, impact-adjusted NAV **94,064.64**;
  - equilibrium + raid stop: 1 win, NAV **103,039.04**.
- Controlled week `2025-01-25` retained large external-draw winners, but the
  equilibrium contract removed them because the old near-edge entry left too
  little costed reward to midpoint.
- Direct displacement-zone invalidation was not executable in some cases: when
  the old retrace entry filled, the proposed stop was already inside the live
  market and Nautilus rejected the protective order.

### Decision

Source equilibrium can be a valid primary delivery objective, but simply
changing the target does not produce a coherent executable trade.  The old
entry, old raid invalidation and new midpoint objective are not automatically a
viable contract.  v33 was not promoted.

## Infrastructure repair under frozen alpha variables

The first 2022 untouched week exposed official Binance archive rows whose
`open_time` column remained mixed string/integer after header removal.  Signal,
week, costs, risk, entry, stop, target and seed were frozen.  The loader was
changed only to parse the completed archive timestamp explicitly to `int64` and
was regression-tested before rerunning the same weeks.

This was a data-normalization repair, not an alpha change.

## v34 — reclaimed source-boundary retest

### Contract

After failed-auction confirmation, rest the passive parent at the exact reclaimed
source-liquidity boundary which existed before the raid.  Keep the original raid
invalidation.  Independently compare external-draw and source-equilibrium
targets.

### Evidence

The source-boundary entry introduced no new filled losses in the tested weeks,
but it was too deep.  Across the complete matrix, the full source-boundary to
equilibrium candidate produced only one filled trade, which won.  In the strong
`2024-11-21` control week, the existing near-edge external-draw trade produced
2/2 wins and NAV **111,180.94**, while source-boundary variants did not fill.

### Decision

The entry was structurally clean but did not supply enough opportunities for a
day-trading system.  It failed on frequency rather than direction or execution.
v34 was not promoted.

## v35 — displacement Consequent Encroachment entry

### Contract

Replace the existing near edge of the first causal displacement void with its
exact 50% midpoint, ICT Consequent Encroachment (CE).  CE is an auction balance
point, not a fitted retracement percentage.  Keep the original raid stop and
frozen expiry.  Run the exact 2x2 entry/target ablation:

- near edge vs CE;
- independent external draw vs source equilibrium.

### Impact-adjusted weekly evidence

| Week | Near edge -> external draw | CE -> external draw | Near edge -> equilibrium | CE -> equilibrium |
|---|---:|---:|---:|---:|
| 2022-05-16 untouched | 1W, NAV 103,821.77 | 1W, NAV 104,038.66 | no trade | no trade |
| 2022-07-09 | 1W/1L, NAV 101,448.13 | 1W/1L, NAV 101,608.40 | 1L, NAV 96,995.78 | 1L, NAV 96,996.26 |
| 2023-02-08 untouched | 1W, NAV 103,451.86 | 1W, NAV 103,503.88 | no trade | no trade |
| 2023-04-11 | 2L, NAV 94,064.64 | 2L, NAV 94,066.92 | 1W, NAV 103,039.04 | 1W, NAV 103,396.76 |
| 2024-08-27 untouched | 1W/2L, NAV 97,362.22 | 1W/2L, NAV 97,498.04 | no trade | no trade |
| 2024-11-21 | 2W, NAV 111,180.94 | 1W, NAV 103,936.20 | no trade | no trade |
| 2025-01-25 | 2W, NAV 115,362.36 | 1W, NAV 106,453.87 | no trade | no closed trade |

### Decision

CE slightly improved entry price on some external-draw trades, but did not make
source equilibrium cost-feasible often enough under the original raid stop.  It
also reduced fills in strong winning weeks.  Static entry-level relocation was
therefore terminated.  v35 was not promoted.

## v36 — CE touch followed by a second rejection displacement

### State machine

The first displacement became detector confirmation rather than immediate trade
confirmation:

```text
FAILED_AUCTION_CONFIRMED
  -> CE_RETEST_ARMED
  -> CE_RETEST_TOUCHED
  -> CE_REJECTION_DISPLACEMENT_CONFIRMED
  -> SECOND_DISPLACEMENT_RETRACE_PENDING
```

The second completed displacement had to:

- break the completed CE-touch bar in the trade direction;
- satisfy the frozen Candidate 11 displacement-body condition;
- satisfy the frozen directional aggressor-flow condition;
- satisfy the frozen close-location condition.

The final stop was placed behind the actual CE-retest extreme plus the existing
ATR buffer.  The 2x2 ablation compared immediate vs second-displacement entry and
external-draw vs source-equilibrium target.  Equilibrium cells exactly removed
the v29 external-draw dependency because the optional runner was not part of the
primary trade.

### Impact-adjusted evidence

| Week | Immediate -> external | Immediate -> equilibrium | CE rejection -> external | CE rejection -> equilibrium |
|---|---:|---:|---:|---:|
| 2022-07-09 | 1W/1L, NAV 101,448.13 | 1L, NAV 96,995.78 | 1W/2L, NAV 98,470.11 | 2L, NAV 94,066.38 |
| 2022-09-02 untouched | no fills | no fills | no fills | no fills |
| 2023-04-11 | 2L, NAV 94,064.64 | 1W, NAV 103,039.04 | 2L, NAV 94,056.06 | 1W, NAV 102,064.41 |
| 2023-06-27 untouched | no fill | no trade | 1L, NAV 96,969.67 | 1L, NAV 96,969.67 |
| 2024-08-27 | 1W/2L, NAV 97,362.22 | no trade | 1L, NAV 96,996.20 | no trade |
| 2024-11-21 | 2W, NAV 111,180.94 | no trade | no fills | no fills |
| 2025-03-14 untouched | 2L, NAV 94,083.92 | no trade | no fills | no fills |

### State-funnel diagnosis

The state machine executed correctly and produced many `CE_RETEST_ARMED` and
`CE_RETEST_TOUCHED` events, with fewer second-displacement confirmations and
still fewer fills.  The dominant terminal/rejection causes were:

- insufficient all-cost structural R;
- CE-retest expiry;
- primary target reached before entry;
- selected target delivered before the CE state could be opened;
- occasional raid invalidation before entry.

These are coherent market-state outcomes, not engine failures.

### Filled-trade diagnosis

Several losing trades were directionally favorable before later reversal:

- a 2022-07 ETH long reached approximately `1072.50` against a `1073.49`
  equilibrium target, then reversed through the final stop;
- a 2023-06 SOL short moved from approximately `17.5768` to `17.40` against a
  `16.956` target, then reversed and stopped;
- other CE-rejection losses similarly produced favorable delivery before the
  market rebuilt adverse structure.

The second displacement improved causal sequencing but did not by itself solve
selection.  Requiring more entry filters or moving CE again would repeat the
static-threshold failure already observed in v31, v34 and v35.

## Clean conclusions retained

1. **Source equilibrium is a real first-delivery state.**  It converted the
   controlled `2023-04-11` lineage from two external-target losses into one
   profitable primary trade in multiple exact variants.
2. **Primary target, entry and runner must remain separate state machines.**
   Requiring an independent external draw for an equilibrium-only primary trade
   is logically circular; the external draw belongs to an optional later runner.
3. **The confirmation displacement zone cannot automatically be the hard stop.**
   In some fills it was already inside the live market.
4. **Source-boundary entry was too deep; CE entry was more fillable but still
   static.**  Neither supplied a robust frequency/payoff solution.
5. **A second post-CE displacement is logically superior to immediate entry but
   still insufficient.**  It reduced opportunity and retained several losses.
6. **The next unresolved variable is post-entry risk ownership, not another
   entry selector.**  Some losers first developed favorable internal structure
   and only then failed.

## Structural replacement selected for round 3

No additional FAR entry filter is authorized from this evidence.  The next
candidate will keep the v36 CE-retest/second-displacement entry and introduce one
causal post-entry state transition:

```text
POSITION_OPEN
  -> FAVORABLE_INTERNAL_PIVOT_CONFIRMED
  -> ORIGINAL_RISK_RELEASED_BEHIND_CONFIRMED_STRUCTURE
```

Candidate 11 already maintains causally confirmed five-minute internal pivots
with a one-bar right wing.  After entry, the first favorable confirmed higher low
for a long or lower high for a short will transfer protection behind that pivot
plus the existing ATR buffer.  Until such a pivot is confirmed, the original
structural stop remains unchanged.

This is not a fixed breakeven percentage, MFE threshold or score multiplier.  It
changes risk ownership only after the market has created and confirmed new
internal structure in the predicted direction.  The exact ablation will compare:

- original stop for the full trade;
- confirmed-internal-pivot protection;

while entry state, target, costs, risk budget, symbols, week, seed and execution
remain frozen.

Project target remains unmet.  No v33--v36 variant is approved for live trading.
