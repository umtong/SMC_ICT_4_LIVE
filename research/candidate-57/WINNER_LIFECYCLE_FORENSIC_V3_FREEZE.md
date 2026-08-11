# Candidate 57 — Winner15m lifecycle thesis forensic v3 freeze

This experiment does not search a new entry filter, stop, target, holding time,
score or parameter.  It observes the unchanged public `win-boom/BTCquant`
Winner15m source account and asks one causal question:

> Before the source trailing winner engine activates, does the completed
> 15-minute trend state that justified entry cease to exist in a way that
> identifies the eventual hard-stop/no-progress loss family while preserving
> the source trailing winners?

The source-faithful policy remains unchanged:

- 200 completed 15-minute startup candles;
- EMA10/EMA30, MACD(12,26,9), ROC3, ADX14 and volume/SMA20 entry;
- the source condition is evaluated on every completed 15-minute candle;
- 2.5% structural/source stop;
- 8%/5%/3%/0% source ROI schedule at 0/480/1440/4320 minutes;
- trailing activation at +1.8% with a 0.5% gap;
- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT in one continuous account slot;
- current-NAV 3% planned-loss sizing and the existing costs/fills;
- warm-up, runoff and an end-flat account.

No order is added, cancelled, moved or closed by the forensic observer.

## Consumed diagnostic intervals

The first interval is the committed source-fidelity anatomy and is used as a
strict parity reference:

- data: `2025-02-27` through `2025-03-17` UTC;
- entries: `2025-03-03` through `2025-03-09` UTC.

The second interval has already been consumed by the arbitration study, but is
rerun with the full source-fidelity availability and management contract:

- data: `2024-09-05` through `2024-09-24` UTC;
- entries: `2024-09-09` through `2024-09-15` UTC.

Neither interval can become holdout evidence after this forensic inspection.

## Fixed observation clock

The open position is observed on every completed 15-minute boundary.  For each
observation the strategy records, without changing the account:

- entry side and current public source side;
- whether EMA, MACD, ROC, ADX and volume still support the entry side;
- direction-adjusted close return from entry;
- MFE and MAE accumulated since entry;
- whether the source trailing state has activated;
- age in minutes and the continuous source-condition episode identity.

## Predeclared thesis-failure transition

The only tested transition is categorical and uses the strategy's own source
logic:

1. the source trailing state has not activated;
2. the latest completed 15-minute source side is no longer the entry side;
3. the direction-adjusted close return from entry is non-positive.

This means the continuation thesis has disappeared before it created any gross
price edge.  No numeric threshold other than the already-public source rules is
introduced.

## Predeclared predictions

If hard-stop/no-progress trades are failed continuation theses rather than
unavoidable stop noise, then the same transition should:

- occur before at least 50% of hard-stop-like losses in each consumed interval;
- preserve at least 80% of eventual public trailing winners in each interval;
- appear before the terminal loss, not only on the exit bar;
- leave the source trailing winner engine and persistent-condition re-entry
  observations intact.

Each interval must contain at least four hard-stop-like losses and four source
trailing winners for the comparison to be informative.

If the transition removes winners at a similar rate, captures losses in only
one interval, or merely identifies trades after their loss is already realized,
the hypothesis is rejected without retuning.  There is no search over grace
periods, return thresholds, component counts, symbols, directions or horizons.

A policy-fresh account is authorized only if this exact transition satisfies
all predeclared predictions in both consumed intervals.  Integration and long
evaluation remain unauthorized regardless of the diagnostic result.