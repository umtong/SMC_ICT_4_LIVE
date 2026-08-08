# Candidate 15 V7 — Bounded Residual Information Transfer

Candidate 15 continues because V6 produced a material structural improvement but
not a valid system. Restricting the response state to the sole excluded market
changed V5's daily geometric growth from about `-2.18%` to `+0.263%`, with
activity in all six exposed intervals. V6 nevertheless had only 5 wins in 23
trades, a `31.25%` closed-trade path drawdown, concentrated growth and execution
safety failures. It is rejected.

The complete failure lineage is preserved in `V1_FAILURE.md` through
`V6_FAILURE.md`.

## Why V6 still failed

Causal event→plan→order→position→one-minute path analysis found three distinct
problems:

1. A fresh response could replace `accepted_symbols` while retaining the first
   activation timestamp. A five-minute leg partly formed before refresh could
   consume the new ownership state.
2. Sender displacement used ATR from prior completed five-minute bars, while the
   receiver engine inserted its current bar into ATR before testing itself.
3. Catch-up parity was often reached before the original stop but normally
   supplied less than one costed risk unit. It describes transfer completion;
   it is not a sufficient final take-profit objective.

## V7 market process

```text
first completed 5m common-flow event
       using prior completed 5m ATR
                    ↓
second same-direction event within 4h
                    ↓
exactly 3 common sender markets
+ positive median signed progress
+ majority advance and origin hold
                    ↓
new effective evidence boundary
                    ↓
sole excluded market is behind sender median?
       ↙                              ↘
      no                              yes
   NO TRADE                  residual receiver remains
                                      ↓
                 completely post-evidence fresh 5m MSS
                 + directional displacement + strict FVG
                                      ↓
                 receiver body / weakest sender body
                         is in [0.5, 1.0)?
                                      ↓
                 parity unconsumed and 0–1 costed R ahead?
                                      ↓
                      passive CE retracement
                                      ↓
              protected same-leg invalidation
              + live completed-4H/day external target
```

The body interval means the receiver has begun material delivery but has not
become an equal-or-stronger information owner. The parity interval means catch-up
is demonstrably mature but incomplete. Both are exposed mechanism-development
definitions, not independent success evidence.

## Causal and execution contracts

- Every activation or accepted refresh resets the effective evidence timestamp.
- A receiver five-minute bar starting at or before that timestamp is no-trade.
- Sender and receiver displacement use only prior completed five-minute ATR.
- Exactly three sender markets are required; four-market agreement has no
  residual receiver.
- Sender markets, SCDAM, SESSION_I7 and unbounded residual continuations remain
  terminally rejected.
- Entry is post-only limit at the fresh FVG consequent encroachment.
- Stop stays beyond the receiver leg's protected swing or opposing bar.
- Final target remains a causally live completed-4H or previous-day pool; parity
  is state evidence only.
- Sizing remains current whole-account NAV × 3% planned loss.
- Across BTC, ETH, SOL and XRP, at most one pending entry or open position exists.
- NautilusTrader 1.230.0 exclusively owns clocks, orders, fills, fees, margin,
  positions and NAV.

No custom backtester, result-based risk multiplier, leverage cap, fallback target
or post-hoc trade deletion is introduced.

## Development protocol

E01-E06 are fully exposed and reused only to test the causal timing, unit and
transfer-state redesign. The unchanged gate requires at least 15 trades in at
least five intervals, positive costed growth, at least 55% win rate, payoff at
least 1.2, path drawdown at most 20%, non-concentrated growth and complete safety.

```bash
for interval in E01 E02 E03 E04 E05 E06; do
  bash research/candidate-15/run_week.sh "$interval"
done
python research/candidate-15/aggregate_v7.py
```

A promising development result permits only a source freeze and newly
predeclared confirmation. Project success still requires a frozen continuous
Nautilus account, realistic total costs, sufficient independent opportunity,
no liquidation or unrecoverable NAV damage, and long-run costed daily geometric
growth of at least 1%.
