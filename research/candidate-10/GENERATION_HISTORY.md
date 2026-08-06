# Candidate 10 — Controlled Generation History

This file records completed logic results separately from implementation errors. No failed or zero-trade generation is presented as project-goal success.

## Fixed evaluation controls

- First BTC week selected before results: `2023-10-16` through `2023-10-22` UTC.
- Selection population: every Monday from `2022-01-03` through `2024-12-23`.
- Selection seed: `20260806`.
- Data: official Binance USD-M BTCUSDT perpetual daily archives, each verified against its published checksum.
- Evaluation input through v2.3: 11,520 one-minute bars including one warm-up day; zero gaps and zero duplicate timestamps.
- Engine: pinned NautilusTrader 1.230.0 BacktestEngine.
- Starting NAV: `100,000 USDT`.
- Planned trade loss: current whole-account NAV × 3%, including declared fees and execution reserve.
- No arbitrary strategy notional cap, score multiplier, or candidate-specific leverage cap.
- Full and ablation variants always run in separate processes with the same data, seed, risk, costs, and execution assumptions.

## v0 — fixed four-hour auction, market entry after confirmation

- Workflow: `31086615230`
- Commit: `ac6ffbb2e79dfe32997572fb9438a73d632c2791`
- Required ablation: remove acceptance only.

| Variant | Trades | Wins | Ending NAV | Net return | Geometric daily growth | Intraday MDD |
|---|---:|---:|---:|---:|---:|---:|
| full | 12 | 4 | 89,434.54418106 | -10.5655% | -1.5825% | 17.8373% |
| no acceptance | 12 | 3 | 85,513.52615303 | -14.4865% | -2.2108% | 14.4865% |

The full variant produced `+11,932.6572 USDT` of price PnL after modeled slippage but before commissions, while declared commissions/execution reserve were `22,498.1130 USDT`. The ablation produced `+5,501.1057 USDT` before commissions and `19,987.5795 USDT` of commissions/reserve.

**Logic conclusion:** discarded. Confirmation was chased with a market parent, event-extreme stops were too narrow relative to BTC price and round-trip cost, and target eligibility used raw rather than executable reward/risk. Removing acceptance did not repair the candidate.

**Retained finding:** raid → rejection/acceptance → displacement ordering contained some directional information before costs. The state-transition framework was retained, not the execution grammar.

## v1 — cost-qualified passive retrace execution on fixed four-hour pools

- Workflow: `31087947997`
- Commit: `e171eede9d3228d17a144bebc6dbb5f9d5b7d4ac`
- Structural change: post-only retrace parent and target, stop-market, executable cost floor, net reward/risk.
- Required ablation: remove acceptance only.

| Variant | Parent orders | Closed trades | Wins | Ending NAV | Net return | Geometric daily growth | Intraday MDD |
|---|---:|---:|---:|---:|---:|---:|---:|
| full | 26 | 16 | 2 | 81,519.71634505 | -18.4803% | -2.8767% | 33.1673% |
| no acceptance | 21 | 9 | 3 | 99,172.13908378 | -0.8279% | -0.1187% | 10.3769% |

Full gross price PnL before commissions was `+2,911.1541 USDT`; commissions/reserve were `21,391.4378 USDT`. The no-acceptance variant generated `+10,439.0932 USDT` before commissions and `11,266.9541 USDT` of commissions/reserve.

**Logic conclusion:** discarded. The acceptance path caused concentrated losses, and a previous fixed UTC four-hour high/low was not a sufficiently meaningful liquidity coordinate. The cost-aware passive execution grammar was retained.

## v2 — right-confirmed structural swing/equal-level pools

- Workflow: `31088746874`
- Commit: `f6cea338aae8920f518f259fc96455b152141c7a`
- Structural change: 15-minute pivots known only after two complete right bars; prominent single swings or repeated equal-level clusters become pools; source and target must both be active pools.
- Required ablation: disable cluster-based activation only.

Full and ablation were performance-identical:

- signals: 2
- parent orders: 2
- closed trades: 1
- wins: 0
- ending NAV: `97,000.05242548`
- net return: `-2.99995%`
- geometric daily growth: `-0.43418%`
- intraday MDD: `2.99995%`
- order errors: 0
- turnover: `2,001,192.02 USDT`
- reported commissions/reserve: `1,100.3707 USDT`
- price PnL before commissions: `-1,899.5769 USDT`

The detector created 183 pools and 24 raids. Sixteen raids became two-close acceptance, five expired without displacement, three displaced, two armed entries, and one filled.

**Logic conclusion:** the candidate was too sparse, and equal-level cluster activation was not the source of results. Pool creation worked causally, but the full pool-to-pool reversal system had no target path.

## v2.1 — efficient multi-bar displacement path

- Workflow: `31089692736`
- Commit: `a4688204a160d09ee247d0b523d1d33851ff4ac4`
- Required ablation: restore single-candle displacement only.

Full and ablation were again performance-identical:

- signals: 3
- parent orders: 2
- closed trades: 1 loss
- ending NAV: `97,000.05242548`
- geometric daily growth: `-0.43418%`

Two expired raids moved approximately 5.6 ATR and 9.5 ATR away from the sweep but failed the stale whole-window approach extreme. The multi-bar path itself did not change realized signals or NAV.

**Logic conclusion:** multi-bar path representation was not the performance cause and was removed from the active grammar.

## v2.2 — nearest already right-confirmed one-minute approach pivot

- Workflow: `31090481422`
- Commit: `4eee6dca40e7e84af9dc65d4b49c8fa5eac526da`
- Tests: 12/12 passed.
- Required ablation: restore the preceding eight-bar range extreme only.

Full and ablation had identical NAV and realized trades:

- closed trades: 1 loss
- ending NAV: `97,000.05242548`
- geometric daily growth: `-0.43418%`
- causality violations: 0

Full certified five displacements versus four for the ablation. The additional displacement lacked a cost-qualified opposing target and therefore did not become a trade.

**Logic conclusion:** nearest confirmed micro structure was a more faithful and causal BOS/CHoCH representation, but it did not create cost-after alpha. It was retained as a diagnostic improvement, not counted as progress toward the performance target.

## v2.3 — first retrace requires observable micro rejection

- Workflow: `31091331349`
- Commit: `fe46bd1beb682eb141b76b652cf72796da6b9a97`
- Tests: 14/14 passed.
- Required ablation: immediately place the old 61.8% passive parent without retrace confirmation.

| Variant | Signals | Orders | Trades | Wins | Ending NAV | Geometric daily growth | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|
| full confirmed retrace | 0 | 0 | 0 | 0 | 100,000.00000000 | 0.0000% | 0.0000% |
| immediate-entry ablation | 3 | 3 | 1 | 0 | 97,000.05242548 | -0.43418% | 2.99995% |

Full state evidence:

- 24 liquidity raids
- 5 displacement confirmations
- 3 retrace states armed
- 2 corridors touched
- 0 retraces confirmed
- 2 expired without confirmed first retrace
- 1 structurally invalidated before confirmation
- 0 orders and 0 trades
- 0 future-time violations

**Logic conclusion:** the immediate blind 61.8% parent was a real loss mechanism; requiring observable rejection removed the only losing fill. It also removed every opportunity. The full 15-minute structural-pool reversal candidate is therefore discarded for insufficient independent opportunity and zero cost-after growth. No further threshold tuning is justified.

**Retained findings:** causal structural pools, explicit acceptance consumption, nearest confirmed micro structure, cost-aware NAV sizing, passive execution, and the distinction between a retrace location and a confirmed retrace scenario.

## Next structural hypothesis

The failed generations cannot distinguish aggressive liquidity taking that is absorbed from aggressive liquidity taking that efficiently reprices the market because one-minute OHLCV lacks signed executed order flow. The next generation replaces the market representation rather than adding another candle filter:

1. ingest official Binance USD-M aggregate trades as Nautilus `TradeTick` data;
2. infer aggressor direction from `is_buyer_maker`;
3. form causal event-time flow auctions;
4. classify absorption versus efficient repricing from signed notional, price response, and flow reversal;
5. use the retained cost-aware Nautilus order/risk grammar;
6. compare the flow-response feature with a price-only ablation under identical execution conditions.
