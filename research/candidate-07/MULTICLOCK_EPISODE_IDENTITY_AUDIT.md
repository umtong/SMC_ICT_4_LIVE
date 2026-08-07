# Multiclock episode-identity audit

## Why this audit was required

The five-second and fifteen-second execution paths sometimes represented the
same completed source-sweep bar with different raw nanosecond endpoints:

```text
five-second path:     ...699999999 ns
fifteen-second path:  ...700000000 ns
```

A heterogeneous pandas row had allowed an `int64` epoch timestamp to pass
through `float64`, whose precision is insufficient at 2025 epoch magnitudes.
The old multiclock episode key included the raw nanosecond timestamp, so one
physical source event could be treated as two episodes instead of being consumed
by the first completed valid retest.

This violated a core scenario invariant and required a full result audit before
any performance interpretation could continue.

## Correction

The corrected contract is:

```text
episode identity
= source pool id
+ causal completed wall-clock second
+ direction
```

Exact raw nanoseconds are retained in the evidence payload. They are no longer
used to split one physical completed-second event. The implementation also:

- preserves fifteen-second bar timestamps as Python integers before heterogeneous
  row selection;
- records every raw candidate sweep timestamp in the chosen episode;
- counts endpoint-precision collisions;
- raises if one already-consumed source pool reappears in another physical
  completed second;
- keeps signal delivery on the exact observed timestamp;
- changes no market threshold, direction, target, stop, cost, risk or period.

Relevant files:

- `exact_timestamp_context.py`
- `multiclock_ensemble_scenario.py`
- `tests/test_multiclock_ensemble.py`

## Corrected development-week portfolio

One-engine BTCUSDT/XRPUSDT evaluation, `2025-12-22` to `2025-12-29` exclusive:

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

This exactly reproduced the pre-correction portfolio result. Therefore the W1
pass was not created by duplicate episode ownership.

The unchanged cross-symbol W1 screen did change where duplicate endpoints had
actually created extra episodes:

- ETH trades fell from ten to eight after two duplicate-clock episodes were
  merged; the result remained strongly negative;
- XRP remained five trades, four wins and one loss;
- SOL remained five losses;
- BTC/XRP one-engine portfolio remained thirteen trades and passed.

## Corrected preregistered Week-2 portfolio

Corrected source commit: `88a49ff4cc4fc3d9c03f88bb7289de2775bc9a78`

Workflow run: `31203218000`

Period: `2025-07-28` to `2025-08-04` exclusive.

The one-day and three-day event-history replays produced exact signal and trade
identity.

```text
trades                    21
wins / losses              8 / 13
win rate                  38.0952%
net return               -11.0266%
daily geometric growth   -1.6552%
profit factor              0.6986
maximum drawdown          15.0358%
active days                6
weekly gate             FAIL
```

Instrument attribution:

```text
BTCUSDT:  1 trade, 1 win, +3,504.74 USDT
XRPUSDT: 20 trades, 7 wins, 13 losses, -14,531.38 USDT
```

The correction reduced maximum drawdown relative to the first W2 report because
ordering and exact episode ownership changed some path accounting, but it did
not alter the scientific conclusion. The negative XRP expectancy and portfolio
failure remained large and reproducible.

## Parent-external successor after correction

The parent-external experiment changed only tradable source scope from every
local fifteen-second swing to causally confirmed, still-unconsumed one-minute or
five-minute external liquidity. Exact episode ownership was applied before its
results were accepted.

### Development W1

```text
BTCUSDT: 3 trades, 3 wins, +7.4578%, active days 2
XRPUSDT: 1 trade, 1 win,  +2.0567%, active days 1
```

The structural quality was high, but combined opportunity was only four trades
on three days.

### Failed W2 research period

```text
BTCUSDT: 2 trades, 2 wins, +3.5853%
XRPUSDT: 9 trades, 3 wins, 6 losses, -7.0186%
```

The external-source distinction reduced the XRP population from twenty to nine
trades, but the remaining five-second execution paths still had negative
expectancy. Winners and losses overlapped materially in source age, penetration,
flow, MSS delay, retest delay and expected RR. No causal single-threshold repair
was supported.

## Scientific conclusion

Two independent findings must remain separate:

1. **Implementation defect:** raw nanosecond episode identity could resurrect one
   consumed source event across clocks. This is fixed and tested.
2. **Logic defect:** a five-second MSS/retest after a local or parent liquidity
   sweep is often only a microstructural recoil, not sufficient evidence that the
   parent auction reversed. This remained after the implementation correction.

The next hypothesis must therefore change the sequence of structural evidence,
not tune a threshold:

```text
parent external 1M/5M liquidity first touch and failed attack
-> completed 15S protected-swing MSS confirms the parent state change
-> first 5S rejection retest of that same broken 15S boundary times entry
```

This keeps the useful retest transition and fast execution clock while refusing
to let a five-second swing alone declare the reversal state.
