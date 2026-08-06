# NT-LVCFR-v16 flow-absorption reclaim failure

## Status

V16 is discarded after a valid first BTC development-week failure.  The causal
contracts, pinned NautilusTrader 1.230.0 runtime, immutable prepared data,
native orders, fills, fees, funding, positions, margin, portfolio, and NAV all
completed normally.

## First-week evidence — 2024-01-08

- GitHub Actions run: `31114898765`
- Artifact: `8973290917`
- Artifact digest: `sha256:37351b98e99f4f44a4c197319b1e239cf9fbe5f2ecfcef4c4c53dcead48bfb69`
- Causal signals: 23
- Native independent episodes: 20
- Wins / losses: 6 / 14
- Win rate: 30.00%
- Initial NAV: 100,000 USDT
- Final NAV: 97,409.34513695 USDT
- Net return: -2.59065%
- Daily geometric NAV growth: -0.374270%
- Mean episode PnL: -129.5327 USDT
- Maximum drawdown: 10.2414%
- Native orders / positions: 40 / 20
- Entry rejections: 0
- End state: flat
- Single-slot contract: satisfied

This is a logic failure rather than an implementation failure.

## State contribution

| Scenario state | Executed episodes | Wins | Native PnL (USDT) |
|---|---:|---:|---:|
| `FLOW_ABSORPTION_RECLAIM_REVERSAL` | 15 | 4 | -5,354.810374 |
| `EVENT_RANGE_CHOCH_REVERSAL` | 5 | 2 | +2,764.155511 |

The new absorption-reclaim branch was the dominant loss source.  Re-entry into
the event range plus opposite futures and spot aggressive flow did not prove
that the opposite side had obtained durable control.  It frequently occurred
during temporary inventory balancing before the prior direction resumed.

## Core-variable ablation

V16 changes V15 by replacing the failed
`FLOW_CONFIRMED_EVENT_ACCEPTANCE` continuation with
`FLOW_ABSORPTION_RECLAIM_REVERSAL`, while preserving the direct event-range
CHoCH state.  Removing V16's new branch leaves the exact same direct-CHoCH
scenario schedule used by the already executed V15 acceptance-branch ablation:

- same eight source scenario suffixes;
- same direction;
- same confirmation and eligible timestamps;
- same initial stops;
- same structural protection waypoints;
- same entry kind and target mode;
- same costs, 3% native NAV risk, data, and NautilusTrader execution path.

The equivalent controlled ablation result is therefore the V15 acceptance
ablation rather than a redundant engine replay:

- GitHub Actions run: `31114142419`
- Artifact: `8973012126`
- Final NAV: 106,033.62200615 USDT
- Daily geometric growth: +0.840456%
- Executed episodes: 7
- Win rate: 57.1429%
- Mean episode PnL: +861.9460 USDT
- Maximum drawdown: 4.48451%

It restores positive expectancy but fails both the eight-episode and 1% daily
growth gates.  No structural path remains by retaining the V16 family and
adjusting the reclaim threshold.

## What worked

The full opposite-boundary completed close remains useful.  It requires the
entire temporary event range to be traversed and closed through, rather than a
wick, midpoint touch, or same-side aggressive-flow burst.  Across V15 and V16
this state retained positive first-week native expectancy.

The failed branches also yielded an important negative result: aggressive flow
agreement and its subsequent one-minute reversal are both observations, not
terminal state proofs.  The system must distinguish two inventory regimes:

1. OI contraction / deleveraging auctions, where opposite-range traversal or a
   full measured extension can resolve the event; and
2. OI expansion / new-position auctions, where spot-supported external-liquidity
   acceptance is a structurally different continuation process.

## Successor direction

The next candidate must not force more trades from the same OI-contraction
event.  It should retain only the successful strong contraction-auction states
and add an independent OI-expansion event family:

```text
OI contraction event
    -> opposite full-range close: CHoCH reversal
    -> same-side full event-range measured extension: continuation
    -> midpoint-only failure: no trade

OI expansion event
    -> spot and futures flow agree
    -> price displaces through a pre-existing local external boundary
    -> next completed minute holds outside
    -> structurally independent continuation
```

The two families represent different sources of order flow and are routed into
the same one-slot native NautilusTrader portfolio.
