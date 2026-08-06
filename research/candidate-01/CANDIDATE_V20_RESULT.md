# Candidate 01 v20 — Accepted-Swing Invalidation Result

## Frozen question

Does replacing the v17 continuation stop beyond the full initiative/response
path with the protected swing of the completed outside-value response events
make durable acceptance naturally executable after 7 bps per side?

Only continuation invalidation changed. The v17 detector, three-event response,
failure precedence, confirmation, measured target, first TradeTick market
entry, current-NAV 3% risk and four-hour hold were frozen.

## Authoritative first BTC week

- Evaluation: `2025-08-04T00:00:00Z` to `2025-08-11T00:00:00Z`
- Engine: NautilusTrader `1.230.0`
- Data: official Binance Vision USD-M aggregate trades as one-for-one TradeTicks
- Custom fill/PnL/NAV simulation: none

### Full resolved portfolio

| Metric | Result |
|---|---:|
| selected plans | 10 |
| protected continuation stops diagnosed | 11 |
| submissions | 0 |
| closed positions | 0 |
| cost-dominated rejections | 7 |
| insufficient net-RR rejections | 3 |
| total return | 0.00% |
| geometric daily return | 0.00% |

### Continuation-only control

| Metric | Result |
|---|---:|
| selected plans | 9 |
| submissions | 0 |
| closed positions | 0 |
| cost-dominated rejections | 7 |
| insufficient net-RR rejections | 2 |
| total return | 0.00% |

Both paths ended flat with zero global-entry-gate violations, zero protective
order failures and zero liquidation markers.

## Diagnosis

The protected swing generally reduced structural stop distance, but this did
not repair the executable geometry. At a 7-bps-per-side cost contract, the
narrower stop made fixed entry/stop costs exceed the permitted share of planned
loss in seven continuation plans. The remaining plans did not retain 1.35
cost-after reward/risk. Removing the reversal plan changed no conclusion.

Therefore the v17 bottleneck is not solved by choosing a different stop extreme.
Tightening the stop converts a distant-target problem into a cost-dominated
risk-unit problem. Loosening it returns to the original low-frequency v17/v19
geometry. Further stop tuning would be parameter accumulation rather than a new
market explanation.

## Decision

`STOP` — do not open a second week and do not tune the protected-swing buffer.

The next structural question is whether the failed-sweep family omitted the
market-structure-shift state transition. A liquidity sweep and flow reversal
must first displace through the nearest opposing internal pivot; only then can a
retest of that broken pivot define a local invalidation and leave the farther
opposing pivot as external liquidity.
