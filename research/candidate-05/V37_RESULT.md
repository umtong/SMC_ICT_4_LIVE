# Candidate 05 v37 result — SMT session-liquidity divergence

## Decision

**Discard v37 as an active candidate.** The implementation and shared-account
lifecycle passed, but the added SMT branch had two trades, zero wins and
`-5,119.27465407 USDT` cost-after PnL in the first frozen shared-account week.
The candidate therefore failed by market logic, not by replay, order, fee,
position, NAV or global-slot implementation.

Authoritative workflow: GitHub Actions run `31146427223`, source commit
`abc327543587ea16a70655093744759ab9e1afbb`, artifact `8981725655`.
Evaluation: `2023-07-09` through `2023-07-15`, one NautilusTrader account,
BTCUSDT/ETHUSDT/SOLUSDT/XRPUSDT, one global executable entry slot.

## Exact-control result

| Metric | v26 control | v37 |
|---|---:|---:|
| Ending NAV | 102,099.82774083 | 95,082.20298380 |
| Total return | +2.099827741% | -4.917797016% |
| Geometric daily growth | +0.297310408% | -0.717816643% |
| Trades / wins | 12 / 8 | 13 / 7 |
| Profit factor | 1.221428 | 0.651856 |
| Maximum drawdown | 5.356609% | 10.148012% |
| SMT incremental trades / wins | 0 / 0 | 2 / 0 |
| SMT incremental PnL | 0 | -5,119.27465407 |

The v37 total-return delta versus its identical-period control was
`-7.017624757` percentage points. Both runs passed integrity checks. There were
no liquidations, order rejections, order denials or global-slot violations.

## Logic-failure diagnosis

v37 encoded:

```text
local completed-session raid
+ at least two of three peers fail the corresponding raid
+ local boundary reclaim
+ tail-flow improvement and current depth
+ local displacement / CHoCH
+ inherited v26 entry path
```

Both executed SMT trades were ETH reversals. In each, exactly one peer had
already consumed corresponding session liquidity:

1. ETH high raid -> short, while BTC had confirmed the same high-side raid;
2. ETH low raid -> long, while XRP had confirmed the same low-side raid.

The binary `two non-confirming peers` rule therefore mixed two different market
causes: a genuinely isolated local excursion and a partially shared market
move. Local reclaim and CHoCH did not repair that ambiguity.

The same evidence contained three CHoCH-complete events where **all three peers**
failed to confirm the local raid. None became an executed v37 trade:

- one ETH long and one SOL short reached their frozen opposing-liquidity target
  before the inherited retest path produced an executable order;
- one SOL long later invalidated at the original sweep extreme.

This is not permission to lower confirmation thresholds. It shows two separate
requirements for the next hypothesis:

1. prove the raid is isolated from common cross-asset price discovery;
2. define an execution transition appropriate for a fast isolated reversal,
   without assuming a market fill or relaxing the cost-after loss budget.

## Retained observations

- Peer non-confirmation must be unanimous for an `isolated` interpretation.
- Session-level divergence is context, not an order signal.
- Cross-asset price discovery must be separated from temporary local pressure;
  a peer can fail a session sweep while still moving efficiently in the raid
  direction.
- A target reached before entry is a missed scenario, not a retrospective win.
- v37's strictly-prior peer registry, timestamp-order independence, frozen
  target, structural stop, 3% current-NAV sizing and one-account lifecycle remain
  reusable infrastructure.

## Next hypothesis

v38 tests an isolated-session-reversal state:

```text
all three peers fail corresponding session liquidity
+ no two peers continue the raid direction with aligned return, flow and
  existing CHoCH-level price efficiency
+ local reclaim, tail-flow/depth turn and displacement / CHoCH
-> one target-derived GTC limit at the worst price preserving 0.40 post-cost R
```

The price cap is the risk and reward basis. It does not assume immediate
execution and cannot fill worse than the cap. Existing fees, adverse slippage,
structural stop, frozen target, current-NAV 3% quantity and NautilusTrader order
lifecycle remain authoritative.
