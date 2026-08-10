# Candidate 57 — private `ichi_5m` one-minute reconstruction v1

## External discovery signal

Freqtrade Strategy Ninja lists a private, one-minute, spot strategy named `ichi_5m` with an unbiased classification, startup count 96, stoploss -10%, and the following indicator footprint:

- EMA close 12, 24, 48 and 96;
- EMA open 48;
- fan magnitude and fan magnitude gain;
- Ichimoku senkou A/B and leading span A/B;
- `buy_min_fan_magnitude_gain` and `buy_fan_magnitude_shift_value`.

The public monthly table reports positive Binance-USDT and KuCoin-USDT results for every displayed month from May 2024 through July 2025. Binance monthly win rates are approximately 84.4%–89.4%, average trade profit approximately 0.68%–0.75%, average duration 2–4 minutes, and monthly account drawdown approximately 0.44%–2.87%. The source code is private, the universe is broad, up to multiple positions were available, and fixed-stake spot accounting differs from this project. These figures are a high-information discovery signal, not project evidence.

The paired private strategy `ichi_1m` has almost the same indicators but runs on five-minute candles and is classified stalled/negative. The one-minute auction clock is therefore treated as material and is not replaced by a slower interpretation.

## Reconstruction boundary

The private source cannot be recovered by hash or exact indicator-name search. This experiment therefore reconstructs a small set of distinct source-consistent policies instead of pretending to know the hidden code.

All cells use completed one-minute candles, standard causal Ichimoku 9/26/52 with 26 displacement, EMA-close 12/24/48/96, EMA-open 48, fan magnitude `EMA48(close)/EMA96(close)`, fan gain `fan/fan[-1]`, and no chikou span.

### Entry families

1. `anchor_cloud`: EMA12 is above the cloud; EMA12, EMA24 and EMA48 are above EMA48(open); fan is above one, has sufficient gain and is above the prior configured fan values.
2. `ordered_cloud`: `anchor_cloud` plus EMA12 > EMA24 > EMA48 > EMA96.
3. `fast_cloud`: EMA12 and EMA24 are above EMA48(open), EMA12 is above the cloud, and the same fan state is present. This tests whether requiring EMA48 above its open anchor removes too many early transitions.
4. `ordered_no_cloud`: the ordered EMA and fan state without the cloud. This isolates whether the cloud adds information or only delays the same transition.

Level re-entry is the source-consistent default. Mirrored shorts are explicit adaptations and are never described as source-exact.

### Risk geometry

- `source_fraction`: source-like 10% stop and 1% objective.
- `auction_structure`: stop beyond the nearest completed-minute support/resistance among the recent 12-minute extreme, cloud boundary and EMA-open-48, with a causal ATR/minimum-distance buffer; objective remains 1% to preserve the observed outcome scale.

The structural stop is not a risk multiplier or nominal cap. Quantity is still current-NAV 3% planned loss divided by the full expected loss per unit, including costs.

### Management

The 1% bracket objective is the primary exit because the external monthly mean around 0.7% is consistent with roughly 1% gross profit after spot-like round-trip costs and losses. A causal source invalidation exits long when EMA12 crosses below EMA-open-48 or fan falls below one, and exits short symmetrically. A separate tight-trailing cell arms after +0.8% and trails by 0.3%, usable only from the next completed minute.

## Frozen development cells

1. `source_anchor_long`
2. `structural_anchor_long`
3. `structural_ordered_long`
4. `structural_fast_long`
5. `structural_ordered_no_cloud_long`
6. `structural_anchor_both`
7. `structural_anchor_long_tight_trail`

This is a structural comparison, not a threshold grid. Fan parameters are frozen at gain 1.001 and two prior increasing fan observations, a public ichi-family setting associated with higher opportunity density.

## Evaluation allocation

- Development: 2026-02-15 through 2026-02-28.
- Untouched: 2025-09-08 through 2025-09-14.
- Conditional 30-day continuous: 2025-08-01 through 2025-08-30.
- No displayed public-report month is used for development, untouched selection or the conditional 30-day account.

Every case persists every completed trade, exit family, R distribution, symbol/side result and winner-versus-loser entry-state contrasts. Up to two development cells consume untouched data: the best quality cell and, when different, the strongest valid opportunity-density cell. This allocation is not a binary truth claim. A 30-day run is consumed only for a positive untouched survivor.

The strict final pass remains one continuous four-symbol account with after-cost geometric daily growth at least 1%, completed trades at least calendar days, positive expectancy, profit factor above one or no losses, maximum drawdown at most 20%, no liquidation and valid one-slot mechanics.
