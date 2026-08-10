# Candidate 57 — Slope-is-Dope ROI schedule mechanical repair v2

## Why this version exists

The v1 source adapter stored the published ROI schedule in descending elapsed-time order. The reused `_roi_profit_ratio` helper is an ascending-time lookup: it starts from the first row and stops as soon as elapsed time is below a row's minute. At elapsed minute zero, the descending schedule therefore returned the terminal `0.0` ROI row. The resulting trades were mechanically closed around entry and paid costs, so the v1 alpha result is invalid.

## Frozen repair

Only the in-memory ROI schedule ordering changes:

- v1: descending elapsed minutes;
- v2: ascending elapsed minutes, matching the execution helper contract.

The following remain byte-for-byte or parameter-for-parameter unchanged from the v1 campaign:

- public Slope-is-Dope entry conditions;
- completed one-hour candle construction;
- claim and public JSON parameter profiles;
- long, short and both-side variants;
- literal public short rolling-minimum exit;
- source stop, trailing and ROI values;
- current-NAV 3% planned-loss sizing;
- fees, adverse slippage and funding reserve;
- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
- one global pending-entry or open-position slot;
- development, untouched and 30-day continuous intervals;
- promotion and final project criteria.

The same development interval may be rerun because v1 did not evaluate the declared strategy. It evaluated a mechanically malformed ROI lookup. Untouched data was never consumed by v1.
