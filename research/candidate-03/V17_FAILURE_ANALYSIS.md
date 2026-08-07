# V17 failure analysis — Dual-Inventory Auction

## Frozen candidate

V17 separated two inventory regimes under the native NautilusTrader 1.230.0 execution and accounting path:

- OI contraction / deleveraging auction:
  - `FIRST_BREAK_CHOCH_REVERSAL`
  - `MEASURED_ACCEPTANCE_CONTINUATION`
- OI expansion / new-position auction:
  - `SPOT_LED_OI_EXPANSION_ACCEPTANCE`

The frozen source blobs were:

```text
derive_nt_lvcfr_v17_signals.py  fcc05dd19bbfc621226250743979d341a7194bf7
nt_lvcfr_v17_config.json        64c7ef99cc076582ffff59c961208bc09d22cae7
nt_lvcfr_strategy.py            e4d00ae0c6fa1d24198c846bccb247baacdc0456
run_nt_lvcfr.py                 74bb02f1b69ee31ce32ddfa47497bdd9770ac00b
```

No candidate parameter or strategy source changed between the two weeks.

## Development week 1 — 2024-01-08

```text
signals                    24
executed episodes          19
win rate                   52.63%
final NAV                  118,460.87 USDT
after-cost return          +18.4609%
daily geometric growth     +2.4497%
mean episode PnL           +971.62 USDT
mark-to-market MDD         10.52%
```

All three states were positive, but the result was concentrated in a small number of large winners. This was explicitly treated as a warning rather than proof of generalization.

## Development week 2 — 2025-06-23

The same frozen code and cost model produced:

```text
signals                    8
executed episodes          6
win rate                   33.33%
final NAV                  94,655.81 USDT
after-cost return          -5.3442%
daily geometric growth     -0.7815%
mean episode PnL           -890.70 USDT
mark-to-market MDD         10.05%
account flat at end        yes
```

State attribution:

| State | Episodes | Win rate | Native PnL |
|---|---:|---:|---:|
| `FIRST_BREAK_CHOCH_REVERSAL` | 2 | 0% | -3,304.42 USDT |
| `MEASURED_ACCEPTANCE_CONTINUATION` | 1 | 100% | +2,051.96 USDT |
| `SPOT_LED_OI_EXPANSION_ACCEPTANCE` | 3 | 33.33% | -4,091.74 USDT |

The week failed episode count, win rate, after-cost expectancy, and geometric-growth gates. Execution, account flattening, single-slot enforcement, native risk sizing, and drawdown accounting all passed. This is therefore a logic failure, not an implementation failure.

## Required single-variable ablation

Removed only:

```text
SPOT_LED_OI_EXPANSION_ACCEPTANCE
```

All OI-contraction states, parameters, costs, weeks, and native execution remained unchanged.

Result:

```text
signals                    4
executed episodes          3
win rate                   33.33%
mean episode PnL           -455.25 USDT
daily geometric growth     -0.1963%
mark-to-market MDD         7.43%
```

Removing the expansion branch reduced losses but did not recover positive expectancy, adequate opportunity, or the growth target. V17 therefore has no structural path through removal of its largest losing branch.

## Largest failure factors

### 1. Same-direction OI expansion was treated as completed acceptance too early

Two five-minute price/OI expansions, cross-market flow agreement, a local external break, and one completed hold minute did not prove durable acceptance. In week 2 the expansion state lost about 4,092 USDT. One event failed almost immediately, while another required the strategy's failure reversal to recover the initial continuation loss.

The feature set identified aggressive inventory creation, but not whether the new inventory would remain profitable. A large OI increase can also describe late crowded positioning immediately before trapped-position liquidation.

### 2. Full-range CHoCH was not regime invariant

`FIRST_BREAK_CHOCH_REVERSAL` was positive in week 1 but lost both executed episodes in week 2. A completed opposite boundary break was a stronger structural fact than a midpoint reclaim, but still did not distinguish true opposite control from a temporary inventory rebalance.

### 3. Measured acceptance was the only state positive in both weeks, but opportunity was insufficient

`MEASURED_ACCEPTANCE_CONTINUATION` remained positive in both weeks. It required actual event-range extension rather than a local boundary hold. This is the strongest preserved causal element, but by itself it did not provide enough independent opportunities to meet the project target.

## Valid components preserved

- Native NautilusTrader order, fill, fee, funding, position, account, and NAV path.
- Current-native-NAV 3% planned-loss sizing and single-slot enforcement.
- Separation of OI contraction from OI expansion.
- Event-range measured acceptance as stronger evidence than a boundary hold.
- Explicit no-trade for midpoint-only failure.
- State-level attribution and winner-concentration diagnostics.

## Candidate decision

**V17 is abandoned as a complete candidate.**

The next hypothesis must not add a return-fitting filter to V17. It will replace the failed expansion state with a sequential inventory-resolution auction:

```text
OI expansion break candidate
  -> no immediate entry
  -> continuation only after measured extension while OI remains elevated
  -> reversal only after price re-enters the broken range while OI contracts from its event peak and spot/futures flow reverse
  -> otherwise no trade
```

This tests whether newly opened positions remain accepted or become trapped, rather than assuming that initial flow agreement is itself alpha.
