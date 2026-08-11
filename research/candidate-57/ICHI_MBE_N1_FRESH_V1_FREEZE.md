# Candidate 57 — Ichi/MBE N→1 fresh account comparison

This specification is frozen before reading the 2026-04-01 through 2026-04-30 result.
It tests one already-implemented account policy rather than searching thresholds after
seeing PnL.

## Objective

The project needs one four-asset, one-slot system whose after-cost continuous NAV can
compound across changing regimes.  The immediate uncertainty is whether two externally
reused components solve genuinely different states when they compete for the same
account slot:

1. `report_short_level` IchiV2: persistent multi-horizon bearish trend;
2. public MBE2 short RSI/TEMA cross: synchronized overbought exhaustion, actionable
   only when at least two of BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT signal on the same
   completed five-minute boundary.

The experiment does not assume that combining two individually interesting components
must improve the account.  It measures opportunity gain, slot displacement and the
actual trades changed by arbitration.

## Frozen source logic

### Ichi family

The exact previously reproduced `report_short_level` policy is retained:

- completed five-minute candles only;
- one-candle shifted causal inputs;
- no Chikou-span decision input;
- conversion/base/lagging/displacement periods `20/60/120/30`;
- fan shift `3`, minimum fan gain `1.0013`;
- short level trigger;
- 4% underlying structural stop and 8% emergency objective;
- 1.5% ROI in underlying price space, ignored while the source entry state remains
  active;
- public 1.5-hour trend-close source exit;
- no trailing stop;
- maximum family horizon 480 minutes.

### MBE family

The exact public completed-five-minute short entry is retained:

- RSI(14) crosses from at/above 70 to below 70;
- TEMA(9) is above the Bollinger(20) middle and falling;
- positive volume;
- 140 completed five-minute candles of startup;
- source-effective leverage 6.46 only translates the public 22% source-profit stop
  into underlying price geometry;
- source ROI ladder `7.9%, 4.7%, 3.2%, 11%, 0.7%, 0.1%` at
  `0, 15, 41, 114, 180, 420` minutes;
- trailing is disabled because the prior causal anatomy isolated ROI-only management;
- a singleton signal is diagnostic only; an actionable MBE state requires at least two
  same-boundary assets.

No entry, stop, ROI, horizon, side, asset, session or breadth threshold may be changed
after observing this interval.

## Frozen arbitration

Three cells run over the identical data and account contract:

- `ichi_only`: highest source-score Ichi candidate; BTC/ETH/SOL/XRP priority breaks an
  exact score tie;
- `mbe_only`: highest source-score MBE candidate only when the same boundary has at
  least two actionable MBE assets;
- `integrated`: a qualified MBE breadth collision has priority; otherwise the highest
  Ichi candidate acts.

All cells use one continuous USDT account, at most one pending entry or open position,
current-NAV 3% planned-loss sizing, the repository cost/slippage/funding model and
NautilusTrader matching/accounting.

## Fresh interval

- evaluated entries: 2026-04-01 through 2026-04-30 UTC;
- required preceding warm-up is downloaded but not scored;
- universe: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT;
- end-flat cutoff is mandatory.

This interval was not used by the IchiV2 March-development/June-continuous/December-
untouched campaign or the MBE2 February/May/July campaigns.  It becomes development
data after this run.

## Predictions before the result

1. `mbe_only` must have no entries from singleton crosses.  Its completed trades must
   originate only from independently recorded same-boundary breadth collisions.
2. If no breadth collision occurs, `integrated` should be trade-for-trade identical to
   `ichi_only`; any difference would be an implementation error.
3. If collisions occur, every integrated-vs-Ichi difference must be explained by a
   pre-entry MBE priority decision and its account-slot opportunity cost.
4. MBE is reusable only if its selected trades improve after-cost expectancy through
   multiple independent episodes, not through one terminal outlier or fewer recorded
   costs.
5. If MBE priority is harmful, do not tune the breadth threshold on this interval.
   Remove or redesign the state from causal evidence.
6. If Ichi collapses, do not repair it with arbitrary thresholds.  Decompose whether
   the failure is stale trend-state persistence, short-only regime mismatch, late entry,
   management or normal variance.

## Required evidence

For each cell retain:

- complete metrics, strategy diagnostics and all completed trades;
- family, symbol, side, episode, exit reason, PnL, R, MFE/MAE where available;
- source signals, singleton rejections, breadth collisions, dual-family boundaries,
  selected entries and unresolved reasons;
- account validity, order rejections, end-flat state, maximum open positions and
  simultaneous entry intents;
- integrated minus Ichi changes in trades, growth, expectancy, drawdown and every
  displaced/added causal episode.

The project target is reported but is not used as an automatic truth gate: after-cost
geometric daily growth at least 1%, completed independent trades at least the evaluated
calendar days, positive expectancy/PF, no liquidation, no unrecoverable account damage
and a valid one-slot account.

## Next action is determined by mechanism

- Credible positive MBE increment: freeze the exact policy and move immediately to a
  longer continuous interval spanning different regimes.
- No collisions: MBE breadth does not expand the opportunity set here; investigate a
  different independent state rather than lowering the threshold on this data.
- Negative MBE increment: inspect the pre-entry collision state and displaced Ichi
  episodes, then remove or structurally replace the MBE role.
- Positive but weak total system: preserve whichever family has causal after-cost alpha
  and add a genuinely independent state family; do not loosen a good family merely to
  manufacture frequency.
