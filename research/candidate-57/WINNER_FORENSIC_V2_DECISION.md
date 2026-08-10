# Winner15m causal-episode forensic decision

This is an anatomy decision, not a pass/fail gate.  The 2025-03-03 through
2025-03-09 interval is development data because the result and individual
paths have now been inspected.

## 1. What was actually tested

The public `win-boom/BTCquant` Winner15m source uses a 15-minute EMA/MACD/ROC/
ADX/volume entry, 2.5% source stop, 1.8% trailing activation, 0.5% trailing
gap, and an ROI schedule that extends to 4,320 minutes.  The public strategy
also declares 200 startup candles and evaluates its entry column on every
completed source candle.

The earlier project adapter was not source-faithful in four important ways:

1. it converted every continuous source condition into a false-to-true
   transition only;
2. it admitted the indicator mathematical minimum (about 39 source candles)
   instead of the source's 200-candle startup;
3. its reused shell retained only 2,000 one-minute bars, which cannot hold
   200 x 15-minute source candles;
4. it imposed a 360-minute day-trade cap while the source ROI schedule extends
   to 4,320 minutes.

These changes may be useful adaptations, but their results cannot be described
as a reproduction of the public source.

## 2. Accounting and observability defects found

The source-control run submitted 40 entries.  Its positions report contained
39 closed positions and one final open short.  The generic metrics path counted
all 40 report rows as trades and classified the open position's entry fee as a
loss.  Therefore the reported `20 wins / 20 losses / 40 trades` was not a valid
completed-trade count.  The completed result was 39 closed trades, 20 wins and
19 losses, plus one unresolved open position and open/inflight orders at the
end.

The runner's `nautilus_positions_match` check also passed incorrectly because
it compared the trade count to a Nautilus total that included snapshots/open
state.  Subsequent evidence must require an end-flat account and count only
closed positions.

The live strategy did not route or log other symbols while its one account slot
was occupied.  Its diagnostics reported 60 actionable source transitions, but
an independent replay of all four symbols found 164 transitions.  The missing
104 decisions were not absent market opportunities; they were invisible to the
strategy log because another position was open.  This is an observability
error, not proof that those 104 signals should have been traded.

## 3. Causal-episode map

The independent replay found:

- 164 symbol-level source transitions;
- 123 distinct timestamp boundaries;
- 39 entered completed trades;
- 125 unentered transitions;
- 104 unentered because the global slot was occupied;
- 20 rejected by same-boundary cross-symbol arbitration;
- one flat-but-not-entered event requiring order/event audit.

The 39 completed entered trades consisted of:

- 18 source-trailing exits, all profitable;
- 12 approximate source-stop exits;
- 7 forced 360-minute exits, including two small winners;
- 2 fill-risk invalidations.

Thus the gross-profit engine was highly concentrated in entries that activated
and completed the source trailing path.  The gross-loss engine was the hard
stop, no-progress time exit and fill-risk path.  `profit/loss` was not used as
a decision-quality label; these event types identify which code and market
state need separate treatment.

## 4. Winner and loser paths

For the 20 actual winners versus 19 actual losses:

- median five-minute direction-adjusted close return was about -0.072% versus
  -0.095%; the distributions overlap heavily;
- median fifteen-minute close return was about -0.105% versus -0.181%; still
  overlapping;
- median MFE was about +3.24% versus +1.14%; separated later in the path;
- median MAE was about -1.15% versus -3.22%.

A naive five-minute sign filter would remove many eventual winners.  The
current evidence supports a later auction-state/no-progress distinction, not a
simple immediate-green/immediate-red rule.

## 5. The one-slot arbitration is structurally wrong

The adapter's score is a fixed 3.2R source reward plus positive bonuses for
larger ADX, larger absolute ROC and larger volume ratio.  Because all candidates
already satisfy the source threshold, this makes the one-slot router select the
most extended/high-volume member of a simultaneous move.

Across the 29 development collision boundaries:

- current maximum-score selection chose the best diagnostic path only 37.9% of
  the time;
- its diagnostic after-cost win share was 48.3%, mean -0.202% and PF 0.770;
- the same-boundary candidates it rejected had 75.0% positive diagnostic paths,
  mean +0.497% and PF 1.934;
- selecting the candidate with the smallest volume excess produced 72.4%
  positive paths, mean +0.420% and PF 1.758;
- selecting the minimum current score produced 65.5% positive paths, mean
  +0.313% and PF 1.551.

These are post-outcome development diagnostics, not tradable evidence.  They do
show why the existing score should not be preserved: it rewards climax
magnitude even though the source already established sufficient trend,
momentum and participation.  A one-slot adaptation needs to preserve remaining
auction space rather than choose the most extended symbol.

## 6. Non-trades and near misses

There were 431 four-of-five near-miss transitions.  Treating their later paths
as diagnostics only:

- the only missing component being volume: 161 episodes, mean +0.166%, PF
  1.241;
- the only missing component being MACD: 49 episodes, mean +0.044%, PF 1.056;
- missing ROC: PF 0.949;
- missing ADX: PF 0.886;
- missing EMA: mean -0.497%, PF 0.549.

The volume condition may be excluding useful opportunities, while the EMA
alignment appears to protect against a materially different and weaker state.
This does not justify deleting the volume rule after looking at outcomes.  It
justifies a separate causal adaptation—such as the externally reused 0.8
volume-ratio convention—followed immediately by untouched testing.

Short-side near misses were positive on average (PF about 1.212), whereas long
near misses were weak (PF about 0.675) in this development week.  This is more
consistent with a missing higher-timeframe directional/regime router than with
hard-coding `short only` from one bearish interval.

## 7. Slot occupancy

Of 104 transitions hidden by an open position, 53 had positive diagnostic paths
and their aggregate mean remained slightly negative.  At the open-position
level, only six eventual losing positions overlapped a positive best missed
candidate.  Therefore indiscriminately shortening every holding period to
create more slots is not supported.  The next policy must distinguish an open
trade that is still progressing from one whose original continuation thesis has
failed while a better independent episode appears.

## 8. Frozen next research actions

1. Reproduce source availability first: 200 startup candles, every completed
   source candle, a warm-up interval, a run-off interval and an end-flat
   account.  Report raw re-entries separately from independent continuous
   source-condition episodes.
2. Repair closed-trade accounting before using any aggregate metric.
3. Freeze one auction-space arbitration adaptation based on the causal logic
   above, then compare it with the current maximum-score router on untouched
   short intervals.  Do not choose among many variants using the untouched
   result.
4. Test a separately frozen volume-relaxation/regime-routing adaptation; do not
   combine it with arbitration until each contribution is understood.
5. Preserve the source trailing winner engine.  Investigate hard-stop and
   no-progress episodes through component decay, acceptance/rejection and
   cross-symbol state rather than adding a generic early break-even.

The current conclusion is not that Winner15m passed or failed.  The conclusion
is that the previous test mixed a potentially useful high-frequency trend
engine with an unfaithful source adapter, invalid end accounting and an
arbitration rule that selected the most climactic candidate.  Those confounds
must be removed before deciding which Winner components belong in the final
N-to-1 system.
