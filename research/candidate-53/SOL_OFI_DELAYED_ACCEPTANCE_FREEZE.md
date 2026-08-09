# Candidate 53 — delayed true-L1 OFI acceptance freeze

Frozen before reading any April-2024 outcome.

## Development evidence used

Only already-opened SOLUSDT January–March 2024 true-L1 OFI development data
were used. The previously frozen q90 / 240-minute continuation event showed a
persistent SOL effect, but fixed-origin stop/2R management failed.

The development path contains a stronger causal distinction:

- q90 events which had progressed in the OFI direction by more than the full
  21 bp project round-trip hurdle after 60 minutes continued materially over
  the remaining 180 minutes;
- q90 events still negative after 60 minutes had negative subsequent 4-hour
  behavior on average.

For the conservative 240-minute non-overlap development events, the exact
pre-frozen gate `60m progress > 21 bp` selected 19 events. Their return from the
60-minute confirmation point to the original +240-minute horizon averaged
about +68.1 bp gross / +47.1 bp after one 21 bp round trip. This observation is
DEVELOPMENT evidence only.

## Frozen confirmation policy

1. Detect the unchanged true-L1 OFI event from `L1_OFI_FREEZE.md`:
   - causal participation clock from prior seven complete UTC days;
   - normalized Cont-style BBO OFI;
   - absolute OFI >= trailing 90-bar q90;
   - direction = OFI sign.
2. Reserve the causal episode for 240 minutes from event entry proxy; reject
   additional q90 events during that episode. This is a conservative
   independence rule, not a trade-count optimizer.
3. Do **not** enter at the original next-minute proxy.
4. Observe exactly 60 completed minutes after that proxy.
5. If direction-normalized log price progress from the original proxy to the
   +60-minute open is **strictly greater than 21 bp**, classify the auction as
   `PRESSURE_ACCEPTED`; otherwise `NO TRADE`.
6. `PRESSURE_ACCEPTED` entry proxy = that +60-minute open.
7. Diagnostic exit = the original event's +240-minute open, i.e. 180 minutes
   after delayed entry.
8. Charge 21 bp round-trip cost once to the delayed trade.

No April result may change q90, the 60-minute confirmation delay, the 21 bp
acceptance threshold, the 240-minute event horizon, direction, or episode
independence policy.

This is still an alpha/geometry diagnostic, not the final executable strategy:
it intentionally has no newly invented stop or target. A clean April pass is
required before deriving invalidation/management from January–March development
paths, and that management must then be tested on a later untouched April
window before any NautilusTrader promotion.
