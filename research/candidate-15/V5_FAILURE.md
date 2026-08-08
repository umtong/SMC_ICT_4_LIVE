# Candidate 15 V5 failure and residual-market attribution

## Audited result

V5 corrected the five-minute/one-minute ATR mismatch and required repeated
cross-market flow to produce common price progress. The six exposed development
intervals still failed decisively:

- 61 closed trades;
- 8 wins / 53 losses;
- win rate `13.1148%`;
- payoff ratio `4.1845`;
- weekly-reset NAV multiple `0.3961044676`;
- daily geometric growth `-2.1808%`;
- closed-trade path maximum drawdown `72.8515%`;
- 631 response-qualified initiative activations.

The result rules out the V5 claim that a response-qualified common-flow state is
sufficient context for trading later continuation legs in any of the four
markets.

## Plan-to-position attribution

The persisted V5 artifacts were joined causally using initiative lifecycle,
submitted plan identity, Nautilus opening order ID, and realized position PnL.
Across the 61 filled trades:

| Market relation to the confirming response | Trades | Wins | Win rate | Realized PnL |
|---|---:|---:|---:|---:|
| Market already in `accepted_symbols` | 53 | 5 | 9.43% | `-107,001.215528` USDT |
| Sole market excluded from the three-market response | 8 | 3 | 37.50% | `+42,826.148130` USDT |

This is exposed post-hoc development evidence, not an independent success claim.
It is also concentrated: all three residual-market wins occurred in E01, while
the other five residual fills lost. The figures therefore justify a new
mechanism test, not a performance claim.

## Structural diagnosis

V5 treated the response state as a broad beta permission. A market that had
already supplied displacement, aggressor flow, and price progress could later
emit another FVG leg and be bought or sold again. In practice this was adverse
selection: the state evidence and the traded market referred to the same already
delivered move.

The sole excluded market has a different economic role. It did not participate
in the confirming response. A later independent MSS/displacement/FVG in that
market can represent delayed information delivery rather than continuation of an
already exhausted leg.

The next test therefore changes ownership, not thresholds:

```text
three markets convert common flow into price progress
                    ↓
exactly one market remains outside accepted_symbols
                    ↓
no order in the three already-delivered markets
                    ↓
excluded market later creates its own fresh five-minute auction leg
                    ↓
post-only retracement, same-leg invalidation, live external objective
```

Four-market agreement has no residual receiver and is `NO TRADE`. The detector,
entry geometry, costs, 3% current-NAV risk sizing, and one-global-slot execution
remain unchanged so V6 isolates only the information-ownership hypothesis.
