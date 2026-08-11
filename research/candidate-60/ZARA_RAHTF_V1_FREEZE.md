# Candidate 60 — frozen ZaratustraV5 × RAHTF clean-state experiment

## Why this experiment has the highest current information value

Candidate 57 established two complementary facts.

1. Public ZaratustraV5 is a genuine high-density trailing-winner engine in the project account: the June 2026 one-slot run completed 214 trades and 144 were profitable.
2. The account still lost because partial and near-full-stop losses overwhelmed many small trailing winners. Behaviour-identical lifecycle studies rejected simple first-invalidation and persistence exits, while the all-candidate audit rejected score inversion and located a material common-mode market-state failure.

The missing decision is therefore upstream of stop management and symbol ranking: **is the apparent 5m/15m/30m directional agreement part of a clean higher-timeframe auction, or only a locally aligned move inside a choppy/exhausted regime?**

This experiment reuses the already imported MIT `richkuo/go-trader` regime-adaptive higher-timeframe classifier. It does not invent a new indicator set and it does not tune a threshold on Zaratustra outcomes.

## Complete scenario roles

- **Context / state:** confirmed clean six-hour trend plus signed 100-hour ATR-normalized drift.
- **Entry candidate:** unchanged completed 5m/15m/30m Zaratustra RSI, directional movement and Bollinger-middle agreement.
- **Interaction / transition:** the source state changes from false to true in the independent-opportunity cells. Level cells are retained only to diagnose whether renewed monetization inside one continuous state has value.
- **Entry, invalidation, objective and management:** unchanged public Zaratustra project adapter: source-normalized 2.96% price stop, +0.71% trailing activation, 0.13% trailing distance, 480-minute safety horizon and one-minute next-bar-usable ordering.
- **No trade:** a source-valid candidate is rejected when higher-timeframe context is not ready, the confirmed clean label disagrees with direction, or signed slow drift is insufficient.
- **Arbitration:** unchanged source maximum-score one-slot arbitration. The previous all-candidate audit did not support score inversion.

The RAHTF layer can only reject a source-valid candidate. It cannot create a trade, change side, change symbol score, modify geometry, alter management or rescale risk.

## Reused RAHTF state without threshold search

The state component preserves the imported defaults:

- complete one-hour candles aggregated into epoch-aligned complete six-hour buckets;
- classification period 14 closed six-hour buckets;
- ADX threshold 20;
- absolute return-efficiency threshold 0.05;
- range-efficiency threshold 0.03;
- Kaufman path-efficiency threshold 0.5;
- state transition confirmed only after two consecutive closed six-hour buckets;
- slow drift `(close - close[100h ago]) / (ATR20 × 100)`;
- clean long requires `trending_up_clean` and drift at least +0.10;
- clean short is the symmetric adaptation: `trending_down_clean` and drift at most -0.10.

No post-entry price, future bucket, eventual exit or outcome label enters the state. The strategy retains 16,000 completed one-minute observations because the inherited 6,000-minute buffer cannot make a 100-hour drift feature ready.

## Four development cells

| cell | source semantics | higher-timeframe state |
|---|---|---|
| `edge_control` | one false→true source transition | none |
| `edge_rahtf` | one false→true source transition | frozen clean-state rejection |
| `level_control` | public completed-candle level and possible re-entry | none |
| `level_rahtf` | public completed-candle level and possible re-entry | frozen clean-state rejection |

Only edge cells are eligible for an independent-opportunity claim. Level-cell trades are raw monetization attempts inside continuous source states and are never used to satisfy the project frequency requirement.

## Frozen data allocation

### Development causal comparison

- scored entries: **2025-08-04 through 2025-08-17 UTC**;
- ten preceding calendar days load causal indicator history but cannot open a position;
- two subsequent days allow a position opened inside the scored interval to finish;
- the interval becomes development data immediately after the first result is observed.

### Conditional policy-fresh comparison

- scored entries: **2025-10-06 through 2025-10-19 UTC**;
- identical ten-day warmup and two-day runoff;
- only `edge_control` and `edge_rahtf` run;
- this interval remains untouched unless the development state hypothesis and account result jointly justify consuming it.

## Predicted transaction changes

The clean-state hypothesis is supported only when all of the following occur in the edge comparison:

1. the frozen state rejects source-valid candidates, proving the composition is active;
2. more removed control trades are negative than positive;
3. at least half of shared positive trailing winners remain positive and the strongest control winner is not destroyed;
4. cost-after expectancy, profit factor and continuous NAV improve together rather than only win rate;
5. the candidate remains mechanically valid, ends flat, uses one global slot and exact current-NAV 3% planned-loss sizing;
6. the positive account is not a one-event anecdote: at least seven completed edge trades, maximum drawdown at most 20% and largest-winner share at most 75%.

The development result authorizes the predeclared policy-fresh interval only when the candidate is itself positive after costs and the transaction predictions above hold. This allocation rule is not a declaration that a rejected state variable is universally false.

## Falsification and prohibited rescue

The composition is rejected without retuning when it merely deletes trades, removes trailing winners and losses similarly, leaves the loss tail intact, improves only through one unrelated slot-path outlier, or remains negative after costs.

After observing either interval, do not change:

- RSI 50, DI 25 or source timeframes;
- source stop, trailing values or 480-minute horizon;
- RAHTF period, ADX, efficiency, confirmation or drift thresholds;
- long/short symmetry;
- source score or one-slot arbitration.

A failure sends research to a structurally different market-state solution, not a local threshold sweep.

## What success would and would not mean

A positive policy-fresh result grants **component status only**. It does not authorize a long validation by itself. The next action would be an opportunity-overlap and conflict study with the independently surviving delayed post-cascade jump-reversal specialist, followed by a single continuous one-slot integration test if the two mechanisms provide complementary causal episodes.
