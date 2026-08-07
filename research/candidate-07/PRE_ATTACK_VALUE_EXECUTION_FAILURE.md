# Candidate-07 discarded scenario: pre-attack value as full-position exit

## Decision

The completed fifteen-second pre-attack VWAP remains a useful causal market-state
reference. It is rejected as a **full-position take-profit** for this candidate.
The rejection separates two implementation defects from the later clean logic
result; no contaminated replay is used as the final decision.

## Structural screen which motivated implementation

The pure aggregate-trade path screen observed 16 accepted failed-auction events:

```text
active days                 5
structural TARGET           9
structural STOP             7
gross structural result   +5.939R
median target              0.928R
median MFE                 1.117R
median MAE                 0.698R
```

This screen answered only whether the price path touched the declared value. It
did not imply a positive executable result after round-trip costs.

## Baseline NautilusTrader failure

The same scenarios were submitted through NautilusTrader with current-NAV 3%
loss budgeting, taker fees, adverse ticks and historical funding.

```text
trades                         16
wins / losses                3 / 13
final NAV             83,186.98 USDT
net return                  -16.813%
daily geometric growth      -2.595%
profit factor                 0.1658
maximum drawdown              17.446%
```

Replacing the VWAP with the completed bucket close changed only the target
statistic and also failed:

```text
trades                         11
wins / losses                 2 / 9
final NAV             87,615.82 USDT
net return                  -12.384%
daily geometric growth      -1.871%
profit factor                 0.1189
```

## MIT execution control and implementation error

A market-if-touched take-profit was tested at the unchanged target to distinguish
passive-limit nonfill from signal failure. The raw MIT replay reported:

```text
trades                         16
wins / losses                3 / 13
final NAV             78,418.33 USDT
net return                  -21.582%
daily geometric growth      -3.413%
maximum drawdown              24.539%
```

This replay exposed a real implementation defect. One target rounded to the
entry tick. The market parent filled 44.158 BTC, the immediately triggered MIT
child closed only 0.245 BTC, and 43.913 BTC remained as an untracked lifecycle
until a two-hour close. The NAV included the orphan loss but the strategy trade
ledger did not. Therefore these MIT metrics are retained as debugging evidence,
not as the authoritative economic result.

## Controlled implementation correction

Two non-alpha controls were added:

1. a target must have strictly positive expected per-unit gain after the same
   adverse entry/target ticks, taker fees and funding reserve used by sizing;
2. execution invariants require one parent fill, one position lifecycle and one
   fully attributed NAV change for every recorded trade.

No empirical R threshold, fitted target, notional cap or risk multiplier was
introduced. Eleven of the sixteen targets were rejected before order submission
because even a successful target fill was expected to lose money after costs.

The corrected W1 replay passed every execution/accounting invariant:

```text
trades                          5
wins / losses                 3 / 2
active days                     2
final NAV             98,834.79 USDT
net return                   -1.165%
daily geometric growth       -0.168%
profit factor                  0.7551
maximum drawdown                4.646%
NAV attribution gap       ~0.000000
```

## Logic failure

The clean result remains negative and fails opportunity, activity and growth
gates. The dominant issue is not the take-profit order type. It is the economic
role assigned to pre-attack value:

- many value targets are smaller than round-trip execution cost;
- a nearby normalization reference cannot compensate for a complete structural
  invalidation often enough;
- using it as a full exit truncates the move immediately after the failed
  auction has only returned to its origin.

The full-exit scenario is therefore retired. Lowering a minimum R, changing the
risk percentage, or extending the value target beyond the measured reference
would not repair the market logic and was not attempted.

## Valid components retained

- pure volume-time failed-auction detector with no position or target state;
- pre-attack value computed only from a completed bucket before contact;
- event-time causality and one episode per liquidity contact;
- target cost viability as an infrastructure prerequisite;
- current-NAV 3% loss budgeting;
- execution and NAV-attribution invariants;
- value delivery as a possible **state transition**, not a profit objective.

## Successor hypothesis

The next independent scenario treats value delivery as normalization evidence:

```text
liquidity raid and failed auction
-> completed return through pre-attack value
-> displaced market-structure shift
-> causal FVG first retest
-> entry from internal liquidity
-> exit toward pre-existing opposing external liquidity
```

The baseline requires the value-normalization milestone. Its single controlled
ablation removes only that milestone while preserving detector, MSS,
displacement, FVG, retest, stop, target and slot contracts.
