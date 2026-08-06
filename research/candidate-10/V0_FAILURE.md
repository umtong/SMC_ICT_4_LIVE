# Candidate 10 v0 — Discarded Execution Grammar

Candidate 10 v0 is discarded. It used a market parent after confirmation, a narrow event-extreme stop, and raw price reward/risk even though its prose described a cost-aware gate. The controlled first-week run and the required one-variable ablation show that this version has no acceptable cost-after path.

## Controlled evidence

- Workflow: GitHub Actions `31086615230`
- Commit: `ac6ffbb2e79dfe32997572fb9438a73d632c2791`
- Week selected before results: `2023-10-16` through `2023-10-22`
- Data: 11,520 Binance USD-M BTCUSDT 1-minute bars, including one warm-up day
- Data integrity: every ZIP matched Binance's published checksum; zero gaps and zero duplicate timestamps
- Engine: NautilusTrader 1.230.0 BacktestEngine
- Starting NAV: `100,000 USDT`
- Risk budget: current NAV × 3% per trade
- Declared cost metadata: maker 4 bp, taker 7 bp, deterministic one-tick adverse slippage

| Variant | Trades | Wins | Net NAV | Net return | Geometric daily growth | Intraday MDD |
|---|---:|---:|---:|---:|---:|---:|
| full | 12 | 4 | 89,434.54418106 | -10.5655% | -1.5825% | 17.8373% |
| no-acceptance ablation | 12 | 3 | 85,513.52615303 | -14.4865% | -2.2108% | 14.4865% |

Scenario attribution in the full variant:

- acceptance: `-8,778.36678844 USDT`
- rejection: `-1,787.08903050 USDT`

There were no order denials or rejections in either controlled variant. The ablation ran in a fresh process with the same data, seed, risk, costs, thresholds, and Nautilus execution assumptions; it changed only `enable_acceptance=False`.

## Dominant failure mechanism

The directional path was not wholly random before commissions, but its economic scale was wrong:

| Variant | Price PnL after modeled slippage, before commissions | Reported commissions | Net PnL |
|---|---:|---:|---:|
| full | +11,932.6572 | 22,498.1130 | -10,565.4558 |
| no-acceptance | +5,501.1057 | 19,987.5795 | -14,486.4738 |

The most influential factors were:

1. **Confirmation chasing.** The market parent entered after the informative move rather than at a structural retrace price.
2. **Cost-dominated invalidation.** Stops as narrow as a few dollars around BTC near 28–30k created very large quantities. The planned 3% price loss was joined by entry and exit costs large enough to dominate the account outcome.
3. **Raw rather than executable reward/risk.** v0 selected targets using geometric distance but did not reject trades whose net reward was consumed by maker/taker costs and execution reserve.
4. **Generic liquidity coordinates.** Every previous fixed four-hour high/low was treated as sufficiently meaningful without requiring swing confirmation, repeated equal-high/low clustering, or evidence that the boundary actually concentrated liquidity.

## Valid part retained

The raid/acceptance/rejection state sequence generated positive aggregate price PnL before commissions in both variants. This is not proof of tradeable alpha, but it is evidence that the causal event ordering contained some directional information. The retained research value is therefore the state transition and structural target framework, not the v0 entry, stop, or generic-pool assumptions.

## Structural response

v1 changes the execution grammar under controlled data and risk conditions:

- no market parent after confirmation;
- rejection entry rests at the 61.8% displacement retrace;
- acceptance entry rests at the accepted boundary;
- entry and target use post-only limits;
- stop remains stop-market;
- invalidation distance is outside both event noise and an executable round-trip cost floor;
- target eligibility uses net reward after declared entry, target, stop, and tick reserves;
- unfilled parents expire or cancel on structural invalidation and scheduled flat windows.

This is a structural correction, not a parameter search. If the same fixed four-hour pools remain weak after v1, the pool-generation hypothesis will be discarded rather than tuned by week.
