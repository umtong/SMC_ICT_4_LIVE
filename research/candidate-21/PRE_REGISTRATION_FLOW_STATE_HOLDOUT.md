# Candidate 21 state-resolved flow sweep — untouched BTC holdout reservation

Reserved before any Candidate 21 read, download, feature build, signal count, trade
count, PnL or chart inspection for this interval.

## Frozen implementation

- Alpha/execution code commit: `5609a32671d01ca2c610c415c590fd61cbdc003f`
- Strategy: `Candidate21FlowStateStrategy`
- Config: `research/candidate-21/config.json`
- Instrument: `BTCUSDT-PERP.BINANCE`
- Engine: NautilusTrader `BacktestNode`
- Risk: 3% of current continuous account NAV per planned structural loss
- Parent: full-hour first completed 10-second balance attack
- Response: immediate next non-overlapping completed 10-second interval
- State:
  - response directional normalized flow persists/intensifies relative to parent; or
  - response directional return and price efficiency both strictly improve
- Context: completed response close agrees with causal one-hour and three-hour
  price discovery
- Entry: GTC market order consuming successive external 10-second bar volume
- Protection: full planned reduce-only stop-market behind the strictly prior
  one-hour opposite extreme plus 0.08 times prior 30-minute ATR
- Exit: four hours or before funding; no arbitrary price target
- Fees/slippage: unchanged project cost assumptions

No threshold, branch, stop, risk, execution, exit or sizing change is permitted
after this reservation and before the result is recorded. A runtime defect that
requires economic-logic changes invalidates this reservation; it does not permit
silent repair and reuse of the same interval as untouched evidence.

## Reserved interval

- Build/warm-up: `2024-02-09` through `2024-02-18` UTC
- Evaluation: `2024-02-12` through `2024-02-18` UTC
- Continuous account: one account from evaluation start through evaluation end
- Data: verified Binance USD-M `aggTrades`; external 10-second bars and actual
  source-derived volume only

Repository search before reservation returned no occurrence of `2024-02-09` or
`2024-02-12`. Candidate 21 has not inspected this interval. Other project work is
not treated as Candidate 21 development evidence unless imported into this
candidate.

## Required report

The run must publish all of the following without selection:

- starting and ending NAV
- total and geometric daily return
- every trade, win/loss and realized PnL
- drawdown, profit factor and largest-winner concentration
- state-route counts and no-trade counts
- planned versus filled entry quantity
- order rejection, liquidation, position-count and protection diagnostics
- exact data and run manifests

The holdout is evidence about this frozen candidate, not permission to tune the
candidate to the interval.
