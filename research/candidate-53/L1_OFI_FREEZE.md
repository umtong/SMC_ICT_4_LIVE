# Candidate 53 — Frozen true-L1 OFI transfer

Frozen before opening the later January, February, or March 2024 windows.

## External mechanism

Tony Li (2026) reports that Order Flow Imbalance on participation/dollar-volume
bars in CME Ether futures has a delayed positive impact profile which peaks near
four hours.  Candidate 53 reconstructed true top-of-book Cont-style OFI from
Binance USD-M `bookTicker` events and used a causal dollar-volume participation
clock of approximately 30 bars/day, matching the external study's reported bar
count per calendar day.

## Development observation used to choose one rule

Only 2024-01-08 through 2024-01-10 was inspected when this rule was frozen.
For BTCUSDT, SOLUSDT, and XRPUSDT, the same external-theory setting survived the
project's 21 bp round-trip hurdle in non-overlapping diagnostics:

- trailing absolute-OFI tail: >= per-symbol 90th percentile;
- direction: continuation in the sign of true L1 OFI;
- holding horizon: 240 minutes;
- decision: only after the participation bar completes;
- entry proxy: strictly next one-minute open;
- no overlapping same-symbol 240-minute positions in mechanism evidence.

No probability threshold, asset-specific direction, alternative horizon, or
quantile may be selected after viewing later windows.  ETHUSDT's Jan-08..10
job had not completed at freeze time and therefore did not influence the choice.

## Promotion sequence

1. Same frozen rule on later January 2024.
2. Same frozen rule on February 2024.
3. Same frozen rule on March 2024 untouched by rule selection.
4. If the mechanism remains cost-positive with sufficient opportunity under a
   four-asset global one-position arbitration, convert it to a complete
   execution policy in NautilusTrader.  Only that NautilusTrader continuous NAV
   can count as strategy/account proof.

A later implementation correction which does not alter the economic rule is
allowed and must be labelled as such.  Any change to tail, direction, horizon,
entry timing, or event construction after opening later windows makes those
windows development data.
