# Candidate 01 v17 result — discarded at the first BTC week

## Frozen protocol

- Candidate: causal three-notional resolved impact
- First frozen BTC week: `2021-01-25` through `2021-02-01` UTC
- Daily event clock: preceding completed UTC day's 20-minute quote notional
- Context: three completed days before evaluation
- Engine: NautilusTrader 1.230.0 on official Binance Vision USD-M aggregate trades as `TradeTick`
- Risk: current Nautilus NAV × 3% planned loss
- Cost: 7 bps per side
- One global pending entry or position

No second or third week was opened because both predeclared first-week rules stopped.

## Authoritative first-week gates

| Rule | Resolved plans | Trades | Win rate | Total return | Geometric daily | Profit factor | Max drawdown | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| resolved-full | 14 | 5 | 60.00% | +1.0298% | +0.1465% | 1.1657 | -5.9862% | stop |
| resolved-reversal-only | 4 | 3 | 66.67% | -0.9276% | -0.1330% | 0.6908 | -3.0772% | stop |

Both runs ended flat with zero global-entry violations, zero protective-order failures and zero liquidation markers. The weekly failure is therefore not an engine failure.

## Controlled diagnosis

The full portfolio did not lose money, but it did not produce enough independent cost-net opportunities or growth. Twenty-three initiatives resolved as:

```text
11 durable continuation
5 failed-impact reversal
7 unresolved
```

Fourteen plans remained inside the evaluation week, but only five reached Nautilus submission:

```text
8 rejected for insufficient delayed net reward/risk
1 rejected while a global position was occupied
5 submitted and closed
```

Executed continuation responses were net profitable:

```text
+5,130.88 USDT
-3,155.21 USDT
net +1,975.67 USDT
```

Executed failed-impact reversals were net negative:

```text
-3,059.10 USDT
+429.82 USDT
+1,683.42 USDT
net -945.86 USDT
```

Removing continuation therefore worsened the result. The first-week weakness is not “continuation contaminated a good reversal rule.” The surviving direction logic is mixed, while a larger immediate bottleneck is that a locally measured target becomes too close after response confirmation and real next-trade entry.

## What is retained

- A completed initiative must not be traded before its post-impact response is known.
- Durable outside acceptance and failed impact are mutually exclusive responses.
- The preceding-day 20-minute quote-notional clock preserved more expectancy than previously tested 10-minute, cost-floor, adaptive multiscale and fixed-time alternatives.
- Three days of causal context correctly initialized the live state at the first evaluation event.

## What is rejected

- v17 as a complete candidate;
- reversal-only resolved impact;
- shrinking the information clock to manufacture frequency;
- treating the local measured structure edge as the only possible final liquidity objective.

The next controlled candidate keeps the state machine and changes only target routing: measured travel is a minimum expectation, while the final target may extend to the nearest farther, already-confirmed and still-unconsumed intrinsic external pool.
