# Candidate 60 — frozen forced-flow release experiment

## Market mechanism

A large completed price impulse is not, by itself, a reason to trade against it.
The same chart move can be produced by two economically different states:

1. **position closure / forced deleveraging** — outstanding contracts are
   extinguished while urgent traders cross the spread; once that flow is
   absorbed and reverses, price can be reaccepted into the prior auction;
2. **fresh position construction** — open interest grows while aggressive flow
   pushes price; a first reclaim can be only a pause inside continuing price
   discovery.

The existing delayed jump specialist already solved one part of the problem:
it waits at least two completed five-minute bars and requires price to reclaim
the terminal jump-candle extreme. Its latest untouched account improved from
9 trades, 3 wins / 6 losses and -13.44% to 3 trades, 2 wins / 1 loss and
+4.46%. Open-interest *stability after the boundary* added no information: the
price-only and OI-stable cells were identical.

This experiment therefore does not tune the jump threshold, stop, target or
management. It tests a more faithful lifecycle distinction using Binance's
strict-as-of open-interest and taker buy/sell metrics.

External motivation:

- Binance USD-M metrics expose total open interest and the taker buy/sell volume
  ratio at five-minute cadence.
- Open interest falling over an impulse means contracts were closed on net;
  rising open interest means contracts were created on net.
- Order flow is persistent because large orders are commonly split, but the
  persistence is regime-dependent; a sign change in aggressive flow is thus a
  meaningful state transition rather than an arbitrary oscillator threshold.

The external references are discovery inputs, not proof. The project account
is authoritative.

## Frozen causal observations

For a source jump ending at timestamp `t0` and a delayed confirmation at `t1`:

- `OI_pre` is the latest target-contract metrics row at or before
  `t0 - 240 minutes`;
- `OI_source` and `Taker_source` are the latest target-contract row at or before
  `t0`;
- `OI_confirm` and `Taker_confirm` are the latest target-contract row at or
  before `t1`;
- every row must be no more than ten minutes old;
- no future row, peer-contract OI, eventual price path or outcome is consulted.

The source horizon itself is 240 minutes, so the OI comparison uses the same
auction leg as the price event.

Definitions:

- `oi_unwind = OI_source / OI_pre - 1 < 0`;
- for a proposed **long reversal** after a downward jump:
  `Taker_source < 1` and `Taker_confirm > 1`;
- for a proposed **short reversal** after an upward jump:
  `Taker_source > 1` and `Taker_confirm < 1`.

The taker ratio boundary of `1` is the exchange-defined neutral point between
aggressive buy and sell volume. The OI boundary of `0` is the accounting
boundary between net contract creation and extinguishment. Neither is fitted
on project outcomes.

## Frozen cells

All cells wait at least ten completed minutes, evaluate only on completed
five-minute boundaries, and expire after fifteen completed minutes.

| cell | additional state required after price reclaim |
|---|---|
| `price_confirmation_control` | none |
| `oi_unwind` | target-contract OI fell over the full 240-minute source leg |
| `oi_unwind_flow_flip` | OI fell and terminal aggressive flow crossed from the impulse side to the reversal side of neutral |

The stricter cells can only delay or reject a price-confirmed source candidate.
They cannot create a trade, change direction, choose a different symbol, reset
the source clock, or alter geometry and management.

## Frozen evaluation intervals

Development account:

- scored entries: **2026-04-06 through 2026-04-19 UTC**;
- sidecar begins two days earlier for strict-as-of joins.

Conditional policy-fresh account:

- scored entries: **2026-06-08 through 2026-06-21 UTC**;
- it is consumed only when at least one forced-flow cell changes actual account
  decisions and the development trade-by-trade evidence supports the stated
  mechanism.

Both intervals, all policies and all comparison rules are fixed before either
result is read.

## Unchanged source, execution and risk contract

- completed four-hour return with absolute prior-only z-score at least 2.0;
- 18 prior completed four-hour returns for volatility;
- initial side opposite the completed impulse;
- peer-taker conditional one-slot arbitration from the preserved source;
- both directions and all four symbols remain eligible;
- one causal event per simultaneous four-hour boundary;
- structural stop includes any post-jump extension;
- original 240-minute event clock is not restarted;
- transient protection arms at +0.4R and escapes at +1.0R;
- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
- one global pending entry or open position;
- current-NAV 3% planned-loss sizing;
- project fees, adverse slippage, funding safety and NautilusTrader matching.

## Interpretation before policy-fresh consumption

The development result is not reduced to a single numeric gate. Every actual
trade and every changed slot path is inspected. A forced-flow cell is eligible
for the predeclared fresh interval only when:

1. it actually rejects or delays at least one price-confirmed opportunity;
2. it completes at least two independent trades, so an empty filter cannot win;
3. its cost-after continuous account return improves and drawdown does not
   worsen versus the price-confirmation control;
4. trade-key comparison shows that removed/degraded opportunities are more
   often losses than wins, or shared opportunities improve without sacrificing
   the control's best positive trade;
5. mechanics, end-flat state, one-slot and 3% risk contracts remain valid.

A fresh success grants component evidence only. It does not authorize long
validation or final-system integration by itself. A causal failure closes this
exact OI/flow policy without changing lookback, neutral boundaries, dates,
source jump threshold or management.
