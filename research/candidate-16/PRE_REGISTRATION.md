# Candidate 16 v1 pre-registration

Frozen before the first Candidate 16 NautilusTrader run.

## Code and data base

- Branch base: Candidate 05 commit `e9c858247ef5247bc3f4d8ad3f0de078a7ecebb0`.
- Engine: existing NautilusTrader path in `research/candidate-05/backtest.py`.
- Strategy: `research/candidate-16/strategy.py`.
- Pure router: `research/candidate-16/effort_result_router.py`.
- Instrument: `BTCUSDT-PERP.BINANCE`, one-minute bars plus causal aggregate-trade/public-depth features.

## First untouched screening interval

- Build/warm-up: 2024-05-03 through 2024-05-12 UTC.
- Evaluation: **2024-05-06 through 2024-05-12 UTC**.
- Continuous account: one 100,000 USDT starting NAV; no daily or weekly reset.

The period was selected before seeing Candidate 16 output. Any rule changed after inspecting this output makes the interval development data; it cannot later be called untouched evidence.

## Frozen rules

- One global pending entry or open position.
- 3% planned loss budget from current NAV.
- Entry/stop costs and adverse slippage included in sizing.
- No arbitrary notional cap, leverage cap, or score-based risk multiplier.
- Maximum three completed bars to classify one parent interaction.
- Failure and acceptance are mutually exclusive.
- Weak reclaim without directional effort is `UNRESOLVED`, not reversal.
- Acceptance requires at least two completed outside closes.
- Failed-auction entry occurs at completed reclaim; acceptance waits only for first qualifying retest.
- Stop uses full observed parent excursion or failed boundary hold.
- Target must be a still-active liquidity pool with at least 1.0 net R after costs; fallback expansion is forbidden.
- Funding blackout/flatten and maximum hold are inherited from the existing day-trading runner.

## Screening gate

The inherited gate is not an optimisation objective. It requires daily geometric growth at least 1%, at least 7 trades and 4 wins, win rate at least 40%, at least 4 active days, max drawdown at most 20%, largest-winner share at most 55%, positive NAV, no liquidation/rejection, one entry intent/position, and Nautilus-generated orders/positions.

## Failure interpretation

- No parents: pool/context or data-contract mismatch.
- Many parents, mostly unresolved: state definition does not fit the observable process.
- Completed states, no entries: natural objective geometry is absent/consumed; do not blindly loosen targets.
- Entries, poor expectancy: state classification or invalidation is wrong.
- High accuracy, low growth: insufficient independent opportunity density or payoff.
- Good first week: screening only; advance unchanged to additional untouched and continuous periods.
