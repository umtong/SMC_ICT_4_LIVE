# NT-LVCFR-v15 flow-impact auction failure

## Status

V15 is discarded as a complete candidate after a valid first BTC development
week failure and the required one-variable ablation.  Both runs used the same
NautilusTrader 1.230.0 order, fill, fee, funding, margin, portfolio, and NAV
path.  No custom backtest engine was used.

## Full first-week result — 2024-01-08

- GitHub Actions run: `31113854950`
- Artifact: `8972890291`
- Artifact digest: `sha256:39faff1bddd34247b4c9bbd5dd8a61e22d5a7ca204ffd322c9f3c932acd045a4`
- Causal signals: 24
- Native independent episodes: 23
- Wins / losses: 4 / 19
- Win rate: 17.3913%
- Initial NAV: 100,000 USDT
- Final NAV: 79,898.65760695 USDT
- Net return: -20.1013%
- Daily geometric NAV growth: -3.15503%
- Mean episode PnL: -873.9714 USDT
- Mark-to-market maximum drawdown: 20.3693%
- Entry rejections: 0
- End state: flat
- Single-slot contract: satisfied

The causal and runtime contract tests passed before this result.  It is a logic
failure, not an implementation failure.

## State contribution

| Scenario state | Executed episodes | Wins | Native PnL (USDT) |
|---|---:|---:|---:|
| `FLOW_CONFIRMED_EVENT_ACCEPTANCE` | 16 | 0 | -24,701.583971 |
| `EVENT_RANGE_CHOCH_REVERSAL` | 7 | 4 | +4,600.241578 |

The largest performance driver was unambiguous: the same-side acceptance state
lost every executed episode.  A completed boundary close, one additional close
outside, and positive cumulative futures/spot aggressive flow did not establish
continuation.  In this event class those observations more often described the
last stage of aggressive chasing into passive absorption.

## Required core-variable ablation

The one removed variable was the entire
`FLOW_CONFIRMED_EVENT_ACCEPTANCE` terminal state.  All source events, reversal
signals, entries, stops, targets, fees, impact, funding, 3% native NAV risk,
data, and NautilusTrader execution/accounting remained unchanged.

- GitHub Actions run: `31114142419`
- Artifact: `8973012126`
- Artifact digest: `sha256:787d6d69f2b990949f3a9f33fda368148db2d602bded3b784d478c3fe113dd04`
- Retained causal signals: 8
- Executed episodes: 7
- Wins / losses: 4 / 3
- Win rate: 57.1429%
- Final NAV: 106,033.62200615 USDT
- Net return: +6.03362%
- Daily geometric NAV growth: +0.840456%
- Mean episode PnL: +861.9460 USDT
- Maximum drawdown: 4.48451%

The ablation restores positive expectancy and recoverable drawdown, proving that
the CHoCH reversal observation contains useful information.  It nevertheless
fails both the minimum-eight-episode gate and the 1% daily geometric growth
gate.  Removing the bad branch therefore does not leave a complete structural
path to the project target.

## What worked and why

`EVENT_RANGE_CHOCH_REVERSAL` required a completed close through the boundary
opposite the original OI-contraction displacement.  This is stronger evidence
than a wick or a same-side breakout: the temporary auction range was traversed
and the opposite side obtained closing-price control.  Four wins and positive
native expectancy support retaining this atomic state for later candidates.

The cross-market flow measurement was also useful diagnostically.  Its failure
was interpretive, not observational: aggressive futures and spot flow should
not automatically be read as price acceptance.  When comparable aggressive
flow produces little durable progress and price subsequently re-enters the
range, the more coherent interpretation is absorption/exhaustion.

## Successor hypothesis

V16 keeps the positive direct CHoCH reversal and changes the failed same-side
state into a sequential absorption test:

```text
same-side break
+ second completed close outside
+ futures and spot aggressive flow agree
    -> aggressive-chase candidate only, no entry

later completed close re-enters the event range
+ futures and spot aggressive flow both reverse
    -> flow-absorption reclaim reversal

no reclaim
    -> no trade
```

A failed continuation attempt is never retried within the same source event.
The successor therefore preserves event independence and uses the failed V15
branch as a causal precursor rather than inverting it immediately.
