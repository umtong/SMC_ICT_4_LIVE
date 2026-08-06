# Candidate 01 v18 result — discarded at the first BTC week

## Frozen protocol

- Candidate: resolved impact with external-liquidity target extension
- First frozen BTC week: `2026-06-01` through `2026-06-08` UTC
- Entry state: candidate v17 causal three-notional resolved impact
- Primary target: nearest farther, already-confirmed and still-open 40-bps intrinsic pivot; otherwise measured target
- Control target: original measured structure target
- Engine: NautilusTrader 1.230.0 on official Binance Vision USD-M aggregate trades represented as `TradeTick`
- Risk: current Nautilus NAV × 3% planned loss
- Cost: 7 bps per side
- One global pending entry or position

No second or third week was opened because both first-week target rules stopped.

## Authoritative first-week gates

| Rule | Plans | Extended targets | Trades | Win rate | Total return | Geometric daily | Profit factor | Max drawdown | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| external-extended | 10 | 3 | 4 | 50.00% | +0.6959% | +0.0991% | 1.1105 | -4.5607% | stop |
| base-measured | 10 | 0 | 3 | 66.67% | +5.4173% | +0.7565% | 2.8046 | -3.1231% | stop |

Both runs ended flat with zero global-entry violations, zero protective-order failures and zero liquidation markers.

## Controlled diagnosis

The primary changed only target routing. Seven of ten resolved-impact plans had no farther open intrinsic pool, so their measured target remained unchanged. Only three targets extended.

The extension did not create enough executable opportunity:

```text
external-extended
    10 plans
    6 rejected for insufficient next-trade net reward/risk
    4 trades

base-measured
    10 plans
    7 rejected for insufficient next-trade net reward/risk
    3 trades
```

The primary added one trade but reduced total return and profit factor. The base target produced a strong three-trade result, but three weekly trades are not a complete day-trading candidate and do not satisfy the frozen gate.

## What is retained

- v17's post-initiative resolution remains more plausible than stale higher-order direction labels.
- The base measured target can produce attractive realized geometry when the entry is favorable.
- Open intrinsic pools are legitimate liquidity objectives, but they are too sparse to solve the dominant execution bottleneck by themselves.

## What is rejected

- external target extension as the missing component;
- opening later weeks from a three- or four-trade first week;
- weakening the cost-net reward/risk gate merely to admit market entries.

The dominant failure now occurs between completed confirmation and the next-trade market entry. Seven of ten otherwise resolved plans in the control lost admissible geometry before submission. The next candidate therefore keeps the signal and target state fixed and changes only entry execution: a NautilusTrader-owned resting limit bracket at the causal confirmation boundary, canceled if the target trades first or the response-time window expires.
