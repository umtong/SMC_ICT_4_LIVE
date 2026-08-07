# Candidate 09 v25 — spot-led liquidation auction reversion

## Economic sequence

1. Freeze the previous completed 15-minute perpetual auction and its volume-weighted equilibrium.
2. Observe a completed five-minute OI reduction whose perpetual impulse breaches that auction with aligned taker flow and participation.
3. Require the perpetual return and basis move to exceed the same completed BTCUSDT spot move by robust pre-shock scales.
4. Do not trade the OI print. Wait for basis contraction, no new perpetual shock extreme, opposite perpetual flow/displacement, and a completed spot structure shift leading the reversal.
5. Trade the perpetual toward the frozen source-auction equilibrium. Invalidate beyond every observed perpetual shock/confirmation extreme.

The target is not futures/spot parity. v24 showed that parity normalized before delayed OI information became tradable. v25 asks a different question: when spot rejects the derivatives repricing, does the perpetual return to the value of the auction it left?

## Frozen controls

- `no-oi`: remove only abnormal OI reduction admission.
- `no-spot-gap`: remove only perpetual/spot dislocation admission.
- `no-spot-lead`: remove only completed spot structure leadership from confirmation.

All numerical thresholds, fixed BTC weeks, full composite costs, 3% planned-loss sizing, one-position contract and conditional 2022–2024 long evaluation remain unchanged from v24.
