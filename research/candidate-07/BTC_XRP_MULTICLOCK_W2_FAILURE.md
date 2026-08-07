# BTC/XRP multiclock portfolio — preregistered Week-2 failure

## Verdict

The BTCUSDT/XRPUSDT multiclock first-retest portfolio is rejected in its current
form. It passed the development week in one NautilusTrader engine, but failed the
first mechanically preregistered untouched week. The failure reproduced exactly
with one-day and three-day event history, so it is a strategy-logic failure, not
a warm-up, state-reset, data, order, or accounting artifact.

The failure does **not** authorize dropping XRP, changing the selected week,
adding a symbol score, or fitting a threshold to Week-2 losses.

## Frozen source and selection

- frozen source: `89e03f9314eab9a456c3fe1cb0d08b2a1190dba6`
- development week: `2025-12-22` to `2025-12-29` exclusive
- preregistered Week-2: `2025-07-28` to `2025-08-04` exclusive
- Week-2 selection method: SHA-256 over the frozen source and fixed label,
  committed before execution in `FROZEN_VALIDATION_WEEKS.md`
- validation workflow run: `31199644281`
- engine: one NautilusTrader `BacktestEngine` 1.230.0
- account: one USDT margin account
- global pending/open slots: one
- planned loss: 3% of current total NAV after estimated fees, adverse ticks and
  funding reserve

## Development-week result

```text
trades                    13
wins / losses              9 / 4
win rate                  69.2308%
net return               +11.3948%
daily geometric growth   +1.5535%
profit factor              2.0012
maximum drawdown          10.5688%
active days                6
weekly gate             PASS
```

The one-day versus three-day context audit produced identical BTC and XRP
causal signals and identical executed trades. The development result was
therefore not created by the one-day event-window boundary.

## Preregistered Week-2 result

```text
initial NAV             100,000.0000 USDT
final NAV                88,972.9508 USDT
trades                    21
wins / losses              8 / 13
win rate                  38.0952%
net return               -11.0270%
daily geometric growth   -1.6545%
profit factor              0.7226
maximum drawdown          25.3211%
active days                6
largest winner share      50.7067%
weekly gate             FAIL
```

Every gate except activity and winner concentration failed:

- daily growth: fail;
- trade count: pass;
- active days: pass;
- drawdown: fail;
- positive NAV: pass;
- one global slot: pass;
- winner concentration: pass.

## Event-context identity

The scored Week-2 population was identical under one-day and three-day event
history:

```text
BTC signals: 2 versus 2, exact identity
XRP signals: 20 versus 20, exact identity
executed trades: exact identity
```

The global slot reserved and released 21 times, ended free, and rejected one
otherwise eligible signal while another instrument held the slot. No second
pending order or position was introduced.

## Instrument contribution

### BTCUSDT

```text
trades            1
wins / losses     1 / 0
net PnL          +3,769.7554 USDT
profit factor     infinity
```

### XRPUSDT

```text
trades            20
wins / losses      7 / 13
net PnL          -14,796.8046 USDT
profit factor      0.4602
```

XRP direction split:

```text
LONG:  10 trades, 5 wins, approximately -2.51% summed trade returns
SHORT: 10 trades, 2 wins, approximately -11.77% summed trade returns
```

Both directions failed. The result cannot be explained as one incorrect weekly
trend bias.

Exit population:

```text
stop-market exits       13
MIT target exits         7
30-minute market exit    1
```

The market exit was profitable. The dominant failure was repeated structural
stop invalidation, not target non-fill.

## Structural population shift

The unchanged XRP detector saw a very different local-liquidity population:

| Diagnostic | Development week | Frozen Week-2 |
|---|---:|---:|
| qualified 15-second sweeps | 44 | 152 |
| first-retest source episodes | 5 | 20 |
| five-second selected entries | 5 | 19 |
| fifteen-second selected entries | 0 | 1 |
| active signal days | 4 | 6 |

The conversion from qualified sweep to entry did not collapse. The population
of apparently qualified local sweeps expanded by more than three times, and the
strategy traded that expansion almost mechanically.

## Why the state definition failed

The current detector calls every causally confirmed 15-second swing a liquidity
source. A first touch with attack flow, finite penetration and a close back
inside becomes a reversal thesis after a local MSS and the first 5-second or
15-second broken-level retest.

That sequence proves a **local recoil**. It does not prove that the market has
completed delivery to external liquidity or reversed the parent dealing range.
The source pool can be internal liquidity inside a larger directional or highly
rotational auction. In a regime which creates many 15-second pivots, the machine
therefore manufactures many nominally independent source IDs while repeatedly
trading the same kind of internal fluctuation.

This explains the observed failure better than a threshold defect:

1. source-pool and entry counts expanded sharply while the code and rolling
   quantiles were unchanged;
2. losses occurred in both directions;
3. winners and losers materially overlapped in expected RR, cost-adjusted target
   geometry, penetration, MSS delay, retest delay and local flow fields;
4. most entries used the 5-second execution clock, but the same clock had worked
   on XRP in the development week;
5. the missing state is not another stronger candle. It is whether the swept
   liquidity is external to a causally completed parent range.

The project principle that a pattern detector must be separated from a trading
scenario was violated here: `15S first touch + recoil` was treated as both the
pattern and sufficient evidence of a reversal scenario.

## Disposition

- Reject the BTC/XRP multiclock first-retest portfolio in its current form.
- Do not execute preregistered Week-3 or the 28-day continuous evaluation for
  this rejected source.
- Do not remove XRP because it lost Week-2; XRP was selected because it won the
  development week, so removing it now would be symmetric outcome selection.
- Do not tune flow quantiles, penetration, wick, MSS, retest, RR or stop-buffer
  thresholds on Week-2.
- Retain the one-engine portfolio-global slot and context-identity audit as
  reusable infrastructure.

## Next independent hypothesis

The next candidate changes one causal question only:

```text
old source: every confirmed 15-second swing
new source: first touch of causally confirmed, still-unconsumed parent external
            liquidity (one-minute or five-minute swing)
execution: unchanged local 5-second / 15-second MSS and first retest
```

A local 15-second sweep may still be recorded by the detector, but it is not a
tradable reversal source unless the event also consumes parent external
liquidity. This is a structural internal-versus-external distinction, not a
Week-2 performance filter.

The new family will treat the development and failed Week-2 intervals as research
periods. Any promotion must use newly preregistered untouched periods and must
again pass one-day versus three-day context identity before performance is
considered.
