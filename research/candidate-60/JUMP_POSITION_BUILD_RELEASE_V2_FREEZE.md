# Candidate 60 — frozen fresh-position build, trapping and release experiment

## Why the preceding market model was rejected

The first forced-flow experiment tested a common interpretation of open
interest: a large price impulse accompanied by falling OI was treated as forced
position closure whose exhaustion should favor reversal. That interpretation
failed on the predeclared development account.

The unchanged two-bar price-reclaim control completed six trades, three wins and
three losses, for +1.628% and PF 1.442. Requiring OI to fall over the same
four-hour source leg retained only one trade, which lost approximately one R,
and removed the control's largest winner. Requiring both OI decline and a taker
neutral crossing retained no trades. The reserved 2026-06-08 through 2026-06-21
interval was not consumed.

Trade-by-trade inspection gave a more specific auction explanation:

- the sole retained OI-decline event was an upward impulse with aggressive buy
  flow still above neutral at the delayed short confirmation; it behaved like
  short covering / contract extinguishment with continuing upward urgency and
  hit the structural stop;
- all five other control opportunities had positive OI change over the source
  four-hour leg, meaning contracts were created on net;
- in those five events, price later reclaimed the terminal jump-candle extreme
  and aggressive flow at confirmation was already on the proposed reversal
  side; the group included all three control winners and two smaller losses.

Therefore the sign of OI change alone does not identify a completed liquidation
cascade. Falling OI can describe continuing forced covering, while rising OI can
create a larger inventory of newly opened positions that becomes trapped when
the auction rejects the impulse.

This experiment tests the corrected lifecycle:

> fresh contracts are built during the impulse
> → the impulse fails to remain accepted
> → aggressive flow is on the reversal side
> → terminal price is reclaimed
> → the newly built inventory can become forced exit fuel.

The April evidence is development evidence only. It motivates this one frozen
policy; it is not used as claimed performance.

## Frozen causal observations

The inherited source and first force-state adapter provide strict-as-of target
contract observations for a source jump ending at `t0` and delayed confirmation
at `t1`:

- `OI_pre`: latest Binance USD-M metrics row at or before `t0 - 240 minutes`;
- `OI_source`: latest row at or before `t0`;
- `Taker_source`: source-boundary taker buy/sell volume ratio;
- `Taker_confirm`: latest ratio at or before `t1`;
- every metrics row must be no more than ten minutes old;
- no future row, eventual path, outcome or symbol exception is consulted.

For a proposed long reversal after a downward impulse, confirmation flow is on
the reversal side when `Taker_confirm > 1`. For a proposed short reversal after
an upward impulse, it is on the reversal side when `Taker_confirm < 1`.

The candidate state requires:

1. `OI_source / OI_pre - 1 > 0`, so contracts were created over the same
   four-hour auction leg as the source price impulse;
2. the delayed confirmation taker ratio is on the proposed reversal side of the
   exchange-defined neutral value `1`;
3. the existing two-completed-five-minute price reclaim is satisfied.

The boundaries are accounting identities, not fitted values: zero separates net
contract creation from extinguishment, and one separates taker buy from taker
sell dominance. The source-boundary flow phase is recorded only for diagnosis.
It is not another requirement: reversal-side aggressive flow may already be
present at `t0`, or may cross after `t0` before confirmation.

## Frozen cells

| cell | state before delayed entry |
|---|---|
| `price_confirmation_control` | two completed 5m bars and terminal-candle reclaim only |
| `position_build_reversal_flow` | control state plus source-leg OI increase and confirmation aggressor flow on the reversal side |

The candidate can only reject or delay a source opportunity. It cannot create a
trade, change the proposed direction, choose a different symbol, reset the
source clock, move the stop or target, alter management, or change risk.

## Untouched policy interval

- scored entry interval: **2026-06-08 through 2026-06-21 UTC**;
- metrics sidecar begins **2026-06-06 UTC** for strict-as-of joins;
- this interval was predeclared for the first forced-flow experiment but was not
  consumed because no April cell earned eligibility;
- no result from this interval is read before this V2 policy and interpretation
  are frozen;
- after the first result, the interval becomes development data for all later
  changes.

## Unchanged source, execution and risk contract

- completed four-hour return with absolute prior-only z-score at least 2.0;
- 18 prior completed four-hour returns for volatility;
- initial proposed side opposite the completed impulse;
- peer-taker conditional one-slot arbitration from the preserved source;
- both directions and BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT remain eligible;
- simultaneous symbols at one four-hour boundary are one causal event;
- delayed cells wait at least ten completed minutes and expire after fifteen;
- structural invalidation includes every post-jump extension;
- the original 240-minute event clock is not restarted;
- transient protection arms at +0.4R and escapes at +1.0R;
- one global pending entry or open position;
- current-NAV 3% planned-loss sizing;
- project fees, adverse slippage, funding safety and NautilusTrader matching.

## Result interpretation frozen in advance

The authoritative result is the actual cost-after continuous one-slot account.
Shadow paths diagnose collision and slot effects only.

The candidate earns **component evidence** only when all of the following hold:

1. implementation, end-flat, one-slot and exact 3% risk contracts are valid;
2. it changes at least one actual account decision;
3. it completes at least two independent trades, so an empty filter cannot win;
4. its total return exceeds the price-confirmation control and its maximum
   drawdown does not worsen;
5. trade-key comparison shows a causal improvement: more removed/degraded
   losses than wins, or shared trades improve without sacrificing the control's
   strongest positive trade;
6. the result is not dependent on a single positive trade.

A success does not authorize long evaluation or final integration by itself. A
failure closes this exact OI-build/reversal-flow policy without changing the OI
lookback, neutral boundaries, source jump threshold, confirmation delay,
geometry, management or interval.
