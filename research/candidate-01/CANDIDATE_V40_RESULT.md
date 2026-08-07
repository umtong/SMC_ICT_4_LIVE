# Candidate 01 v40 — Persistent cross-asset delivery state

## Decision

**STOP after the frozen first week.** Do not open later weeks and do not lower
the cost-after reward/risk gate.

- frozen week: `2025-01-27` through `2025-02-03` UTC
- frozen seed: `4001`
- authoritative run: `31185792092`
- artifact: `8997011933`
- artifact digest: `sha256:364ef9d6a4d889f749c582f7f3121753c1f34ea151a80c28fdc432416dc470ff`
- funnel run: `31187092256`
- funnel artifact: `8997328078`
- funnel digest: `sha256:f722a33a0bc2de6263257afbea76effe74480303b59eba5c6d137ab38423157f`
- engine: NautilusTrader 1.230.0 on official Binance Vision USD-M aggregate trades as TradeTicks
- current shared-account NAV risk: 3%
- cost: 7 bp per side
- one global pending entry or open position

## Frozen comparison

Primary retained each completed peer hourly-liquidity acceptance until that peer
closed back through its frozen breakout boundary. Control required the same two
peer acceptances in the exact laggard signal minute. Direction, laggard internal
break, stop, hourly target, cost, risk and execution were unchanged.

Both variants selected zero plans and executed zero trades. All 10,080 joint
minutes were present; both engines ended flat with zero global-entry violations,
protective-order failures or liquidation markers. The implementation and three
causal state tests passed.

## Frozen funnel

The diagnostic replay changed no strategy rule and created no orders.

| Stage | Count |
|---|---:|
| joint evaluation minutes | 10,080 |
| leader states armed or refreshed | 2,047 |
| long / short two-peer consensus minutes | 617 / 755 |
| peer-consensus symbol checks | 3,990 |
| hourly target still unconsumed | 2,079 |
| aligned internal breaks | 288 |
| aligned flow | 253 |
| aligned close location | 240 |
| expanded range | 189 |
| positive gross geometry | 94 |
| minimum price-risk share passed | 85 |
| minimum 1.35 cost-after reward/risk passed | **0** |

Every one of the final 85 observations failed because the same-direction frozen
hourly boundary was too near after the laggard's completed internal break. The
observed net reward/risk examples were generally far below one even though most
had adequate price-risk share. Lowering the gate would admit structurally weak
trades rather than reveal alpha.

## Interpretation

Persisting leader acceptance solved same-minute synchronization but not economic
geometry. Once two peers had delivered and the laggard finally confirmed in the
same direction, most of the laggard's remaining same-side hourly travel had
already been consumed.

The next independent response is not a farther arbitrary target. It tests a
mutually exclusive auction outcome: when two peers retain one-direction delivery
but a laggard leaves that target unconsumed and displaces through opposite
internal structure with opposite aggressive flow, treat it as failed information
transfer and rotate toward the laggard's opposite frozen hourly liquidity.
