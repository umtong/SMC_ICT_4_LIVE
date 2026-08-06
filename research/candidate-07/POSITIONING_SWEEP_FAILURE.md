# Candidate-07 discarded hypothesis: external sweep + OI-release reversal

## Scope

This note records the positioning-aware external-liquidity sweep candidate tested before the independent balance-auction candidate. The execution layer was NautilusTrader `BacktestEngine`; no separate order, fill, cash, fee, position or PnL simulator was used.

Frozen BTC weeks:

- Week-1: 2025-12-22 through 2025-12-29
- Week-2: 2025-01-27 through 2025-02-03
- Week-3: 2024-06-24 through 2024-07-01

## Hypothesis

A first contact with previously formed external liquidity was classified with completed five-minute price, taker aggressor flow and Binance USD-M open interest:

- external sweep/reclaim + OI release -> liquidation-exhaustion reversal;
- external sweep/reclaim + OI build then release -> trapped-position reversal;
- outside close + OI build -> inventory-backed acceptance continuation;
- outside close + OI release -> covering break, requiring a later retest and fresh OI build.

Targets and stops were fixed at `ENTRY_READY`; next-minute entry was rejected when the remaining reward-to-risk had eroded. Planned loss was sized from current Nautilus account NAV at 3%, including fees, adverse ticks and funding reserve.

## Implementation errors separated and corrected

### Completed-data timestamp alignment

The first replay produced zero trades because Binance kline close timestamps ended at `...59.999000000`, while five-minute positioning snapshots were timestamped at the next exact minute boundary. All 2,880 candidate signal intervals were therefore marked `POSITIONING_SNAPSHOT_MISSING`.

The fix normalized completed flow to the next-minute boundary, delivered the matching bar one nanosecond later, and looked up both completed data items at `bar_time - 1ns`. The same Week-1 was rerun without changing strategy logic.

### Invalid public positioning snapshot

Week-2 initially stopped before Nautilus metrics were produced. The official archive contained one unusable row:

```text
2025-01-28 12:10:00+00:00
sum_open_interest = 0
sum_open_interest_value = 0
```

The exact five-minute interval was made unavailable. It was not interpolated or forward-filled. Any active scenario was terminated at the data gap, and the first later ten-minute OI change was classified neutral rather than compared with normal five-minute impulses. The same Week-2 was then rerun.

## Frozen results after implementation fixes

### Week-1

```text
trades                  7
wins / losses           5 / 2
net return              +14.3162%
daily geometric growth  +1.9298%
profit factor           3.5191
maximum drawdown        4.1905%
active days             6
largest winner share    37.636%
weekly gate             PASS
```

The useful component was selective inventory routing:

- OI-release reversals: 5 trades, 4 wins, about +9.6k USDT;
- inventory acceptance: 2 trades, 1 win, about +4.7k USDT.

### Week-2

```text
trades                  2
wins / losses           0 / 2
net return              -5.5432%
daily geometric growth  -0.8114%
profit factor           0
maximum drawdown        6.527%
active days             2
weekly gate             FAIL
```

Both losses were OI-release reversals. Week-3 was not opened.

## Required single-variable ablation

The only changed variable was `use_open_interest=false`; all price, flow, geometry, execution and cost settings stayed fixed.

```text
trades                  7
wins / losses           1 / 6
net return              -11.3405%
daily geometric growth  -1.7048%
profit factor           0.2512
maximum drawdown        15.2658%
active days             5
largest winner share    100%
weekly gate             FAIL
```

Removing OI substantially worsened selection. OI therefore had real incremental noise-filtering value, but its sign was not sufficient to assign trade direction.

## Actual-path diagnosis

The two Week-2 losses were joined to checksum-verified one-minute bars after their real Nautilus fills:

```text
SHORT loss: MFE 0.572R; reached +0.5R; never reached +1R; stopped after 59 min
LONG loss:  MFE 0.220R; never reached +0.5R; stopped after 6 min
```

This is primarily a direction-classification failure, not a target-distance or protective-stop failure.

## Largest performance drivers

Positive Week-1 performance came mainly from a small number of correctly classified OI-release episodes. Week-2 showed that OI decline does not reveal which side was liquidated or whether the observed release completed the move. The same observable can represent long liquidation, short covering, basis-arbitrage reduction or two-sided deleveraging.

The most important negative factors were:

1. direction was inferred from the swept side plus OI sign rather than from a newly established inventory and its subsequent acceptance or unwind;
2. external four-hour pools produced sparse opportunities;
3. two Week-2 signals had little favorable price progress from inception;
4. removing OI increased frequency only by admitting lower-quality price/flow events.

## Components retained

The following are infrastructure or scenario components, not evidence that the discarded strategy itself works:

- checksum-verified USD-M positioning archive loader;
- completed-time `CustomData` delivery into NautilusTrader;
- no interpolation or forward fill across invalid OI snapshots;
- neutral treatment of non-contiguous OI changes;
- first-contact, one-attempt liquidity/balance lifecycle;
- current-NAV 3% planned-loss sizing after fees, adverse ticks and funding reserve;
- fixed signal-time target and stop with delayed-entry RR erosion rejection;
- branch-level events, trade reports and NAV diagnostics.

## Disposition

The standalone rule `external sweep + OI release -> reversal` is discarded. It must not be restored by threshold tuning.

The next independent hypothesis begins from a completed rotational balance and requires **new OI build** on the initiative break. It then trades either:

- held price acceptance with inventory maintained; or
- a return into balance accompanied by opposite flow and release of the newly built breakout inventory.

This changes the causal question from “which side was liquidated?” to “where did new inventory enter, and was that initiative accepted or trapped?”
