# Candidate 01 v16 result — discarded at the first BTC week

## Frozen protocol

- Candidate: hierarchical external-liquidity auction portfolio
- First frozen BTC week: `2024-08-05` through `2024-08-12` UTC
- Engine: NautilusTrader 1.230.0 on official Binance Vision USD-M `aggTrades` represented as `TradeTick`
- Risk: current engine NAV × 3% planned loss
- Cost: 7 bps per side
- One global pending entry or position

No second or third week was opened because both predeclared first-week rules stopped.

## Authoritative first-week gates

| Rule | Plans | Trades | Win rate | Total return | Geometric daily | Profit factor | Max drawdown | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| hierarchical-full-composite | 38 | 14 | 28.57% | -6.0389% | -0.8859% | 0.7765 | -14.5101% | stop |
| hierarchical-reversal-only | 8 | 7 | 28.57% | -8.1733% | -1.2107% | 0.4219 | -14.4283% | stop |

Both runs ended flat with zero global-entry violations, zero protective-order failures and zero liquidation markers. The failure is therefore strategy logic, not an execution-engine failure.

## Controlled diagnosis

Removing every continuation response did not repair the candidate. The reversal-only control retained seven trades but five lost. The routed reversal state admitted several long reversals while completed 72-hour delivery was strongly bearish because the router used:

```text
delivery acceptable
= delivery is not strongly against the trade
  OR old 160-bps outer state agrees with the trade
```

The second term allowed a stale `BULL` state to override current adverse delivery. In the first primary run, routed long reversals appeared while the aligned 72-hour delivery fraction was roughly -0.69 to -0.84. That is not a minor threshold issue: the state priority encoded “old structural label over current realized delivery.”

## What was useful

The infrastructure behaved causally and consistently:

- completed-event plans were immutable;
- official venue trades drove Nautilus fills and account NAV;
- all seven UTC daily NAV rows were present;
- position sizing followed the fixed 3% planned-loss contract;
- the first-week control isolated the reversal response without changing execution.

## What is rejected

The following research direction is not retained:

- local failed sweep + premium/discount location + outer directional-change label as sufficient reversal evidence;
- allowing a historical outer-state label to waive strong current delivery against the trade;
- adding more location filters to repair the same reversal premise.

The next candidate must obtain direction from the market's **post-initiative response**—durable outside acceptance or failed impact with opposite flow—not from a stale higher-order label.
