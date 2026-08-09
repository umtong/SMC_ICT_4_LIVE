# Candidate 05 v36 result — prior-completed cross-asset repricing veto

## Decision

**Discard v36 as an active trading candidate. Retain only its causal peer-state
registry and same-timestamp exclusion contract as research infrastructure.**

The result is not interpreted as proof that cross-asset information is useless.
It shows that this specific, predeclared state — two of the other three project
instruments simultaneously showing an efficient opposite repricing regime on
their latest strictly earlier completed minute — did not occur at any inherited
v26 reversal entry in the frozen weak shared-account week. Relaxing the flow,
efficiency or depth requirements after seeing the losses would be post-hoc
threshold fitting and is prohibited.

## Hypothesis

A local liquidity sweep and reclaim can be a false reversal when the crypto
market is being repriced in the opposite direction. v36 preserved every v26
local detector, CHoCH, entry, stop, target, cost, current-NAV 3% sizing and
NautilusTrader execution rule. It added only a veto before order submission:

```text
proposed local reversal side = s
systemic repricing direction = -s

latest strictly earlier completed minute from each peer
  -> directional return >= existing acceptance distance
  -> 3m aggressor flow >= 1/3 (exact 2:1 ratio)
  -> price efficiency >= existing acceptance threshold
  -> directional depth >= existing reversal-depth threshold

at least two of the three peers confirm
  -> veto inherited TAIL_FLOW_* reversal order
```

Observations with the same event timestamp were deliberately excluded so that
strategy registration order could not become information.

## Implementation-error separation

Before performance was evaluated, the shared runner exposed two independent
implementation errors:

1. the direct shared entrypoint did not install the existing string-epoch
   timestamp contract, so millisecond epoch strings were parsed as calendar
   years;
2. the shared orchestrator still called the frozen instrument factory with an
   obsolete one-argument contract.

Both were repaired without changing v26 or v36 market logic, symbols, dates,
fees, slippage, risk, order model or account model. The same frozen range was
then rerun successfully through one NautilusTrader `BacktestNode`, one account,
four instruments and the audited global entry lifecycle.

## Authoritative controlled evidence

Workflow run `31144289681`, artifact `8980969945`, source commit
`cd988a1424271590b9b8c84eb204fd81034c0886`.

Frozen shared-account weak week:

```text
2023-09-08 through 2023-09-14 UTC
BTCUSDT + ETHUSDT + SOLUSDT + XRPUSDT
one account / one shared NAV / one executable entry-or-position slot
```

| Metric | v26 control | v36 candidate | Delta |
|---|---:|---:|---:|
| Total return | -15.447385% | -15.447385% | 0.000000% |
| Geometric daily growth | -2.368586% | -2.368586% | 0.000000% |
| Trades / wins | 12 / 4 | 12 / 4 | 0 / 0 |
| Active days | 6 | 6 | 0 |
| Maximum drawdown | 15.831101% | 15.831101% | 0.000000% |
| Profit factor | 0.221612 | 0.221612 | 0.000000 |
| Ending NAV | 84,552.62 | 84,552.62 | 0.00 |
| Order rejections / denials / liquidations | 0 / 0 / 0 | 0 / 0 / 0 | — |
| Reversal evaluations | — | 12 | — |
| Reversals vetoed | — | 0 | — |

Symbol attribution remained unchanged:

| Symbol | Trades / wins | Net PnL |
|---|---:|---:|
| BTCUSDT | 3 / 3 | +2,783.70 USDT |
| ETHUSDT | 5 / 0 | -10,430.45 USDT |
| SOLUSDT | 4 / 1 | -7,800.63 USDT |
| XRPUSDT | 0 / 0 | 0.00 USDT |

Scenario attribution remained unchanged:

| Scenario | Trades / wins | Net PnL |
|---|---:|---:|
| Sponsored CHoCH | 7 / 3 | -8,059.66 USDT |
| Confirmed second touch | 3 / 0 | -6,210.12 USDT |
| Confirmed retrace | 2 / 1 | -1,177.60 USDT |

## Required one-variable ablation

The exact ablation is v26: remove only the cross-asset veto while preserving all
local logic and shared-account machinery. It produced identical orders, fills,
positions, daily returns and NAV. Therefore v36 added neither positive nor
negative expectancy in this range.

## Why the veto did not activate

The gate was exercised on all 12 entries and had at least two eligible prior
peer observations every time. Most losing ETH and SOL entries did not coincide
with two peers showing the complete opposite state. Peer flow was often not
opposite, and when opposite flow was present, contemporaneous price efficiency
or directional depth usually did not confirm a common efficient repricing.

One losing SOL short had two peers with upward 3m flow above the exact 2:1
boundary, but both peers had very low one-minute price efficiency and
non-supportive depth. Removing those confirmation requirements because of that
single loss would convert a structural hypothesis into a fitted loss filter.

## Useful components retained

- A process-local peer registry can coexist with the audited one-entry/position
  lifecycle.
- Only strictly prior completed peer observations are used; same-timestamp
  registration order is excluded and was observed zero times.
- The controlled experiment showed that the weak-week ETH/SOL losses were not
  explained by the predeclared common efficient-repricing state. That narrows
  the next hypothesis toward the local scenario premise or a different dynamic
  state rather than a broad market-beta filter.

## Largest performance driver and next decision

The largest negative driver remains inherited ETH/SOL reversal logic, especially
sponsored CHoCH and confirmed second touch. v36 did not address that driver in
an observable way. It is therefore discarded rather than tuned.

The next independent candidate is the sequential aggressor-flow regime release:
a fixed likelihood-ratio change detector must first establish persistent order
flow, then price efficiency, structure, activity and book withdrawal must make
that pressure real, and only the first defended structural retest may trade.
This tests a different cause from local sweep rejection and does not reuse v36's
failed peer veto.
