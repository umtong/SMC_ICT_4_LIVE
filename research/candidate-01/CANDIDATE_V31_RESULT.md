# Candidate 01 v31 — Failed-Acceptance Reversal Result

## Frozen question

V30 showed that a cost-resolved outside initiative followed by one completed
counterflow pullback did not justify a continuation prior. V31 therefore kept
the initiative and pullback detector but reversed the directional resolution:

1. the pullback must close outside the accepted pre-initiative boundary;
2. primary: a later official venue trade must cross back through that boundary,
   triggering a NautilusTrader `STOP_LIMIT` reversal;
3. control: remove only the later boundary-loss confirmation and enter the same
   reversal at market immediately after the completed pullback;
4. invalidate beyond the completed pullback outside extreme plus one 7-bp
   buffer;
5. target the opposite edge of the completed pre-initiative 20-event structure.

## Authoritative first BTC week

- Evaluation: `2021-10-04T00:00:00Z` to `2021-10-11T00:00:00Z`
- Engine: NautilusTrader `1.230.0`
- Execution data: official Binance Vision BTCUSDT USD-M aggregate trades
  represented one-for-one as NautilusTrader `TradeTick`
- Costs: 7 bps per side
- Planned risk: current Nautilus account NAV × 3%
- Maximum hold: four hours
- Custom fill, PnL or NAV simulator: none

### Primary — later accepted-boundary loss

| Diagnostic | Result |
|---|---:|
| outside initiatives in full context | 93 |
| accepted counterflow pullbacks | 62 |
| evaluation instructions | 45 |
| Nautilus submissions | 8 |
| closed positions | 5 |
| wins | 0 |
| win rate | **0.00%** |
| total return | **-12.4459%** |
| geometric daily return | **-1.8808%** |
| profit factor | **0.0000** |
| maximum drawdown | **-12.4459%** |
| insufficient net-RR rejections | 35 |
| cost-dominated rejections | 1 |
| pre-entry invalidations | 3 |

All five filled positions closed at their structural stop. The three other
submitted stop-limit entries were correctly canceled when the pullback outside
extreme invalidated before entry. The run ended flat with no pending entry, no
global gate violation, no protective-order failure and no liquidation marker.

### Single ablation — immediate reversal after the completed pullback

| Diagnostic | Result |
|---|---:|
| evaluation plans | 45 |
| Nautilus submissions | 20 |
| closed positions | 20 |
| wins | 7 |
| win rate | **35.00%** |
| total return | **+1.2576%** |
| geometric daily return | **+0.1787%** |
| profit factor | **1.0307** |
| maximum drawdown | **-14.6897%** |
| target exits | 6 |
| stop exits | 9 |
| time/other exits | 5 |
| cost-dominated rejections | 21 |
| insufficient net-RR rejections | 2 |

The ablation was weakly positive but did not approach the project gate. The
six target exits gained about 1.28% of NAV per trade on average; the nine stop
exits lost about 0.33% of price return each before portfolio compounding. The
four-hour/other exits were net negative.

A large directional asymmetry was present:

| Reversal direction | Trades | Win rate | Sum of position returns |
|---|---:|---:|---:|
| long | 7 | 57.1% | +3.794% |
| short | 13 | 23.1% | -0.068% |

This week was a strong BTC advance, so this asymmetry cannot be treated as a
general long-only edge. It instead warns that immediate pullback reversal is
highly dependent on the surrounding dealing-range direction and destination.

## Diagnosis

The primary failure is logical, not an implementation error. A single later
trade through the accepted boundary did not establish failed acceptance. It
selected five whipsaws and every one returned through the pullback outside
extreme. The primary also delayed entry while still using a local opposite
20-event edge, causing 35 of 45 opportunities to lose the required cost-after
reward/risk.

The ablation identifies useful but incomplete structure:

- an immediate counterflow pullback can sometimes start a profitable reversal;
- pullback-swing invalidation creates an executable risk unit for a meaningful
  subset;
- target hits are large enough to offset a sub-50% win rate;
- the edge is not symmetric and cannot be interpreted without a broader
  dealing-range / external-liquidity destination;
- adding a one-tick boundary-loss trigger made selection materially worse.

Thus neither `outside pullback -> continuation` nor `one later boundary tick ->
reversal` is a sufficient scenario. The missing state is a completed structural
shift with directional displacement, while the local 20-event opposite edge is
still too shallow as a delayed-entry destination.

## Decision

`STOP` — do not run v31 weeks 2 and 3, and do not tune the boundary offset.

The next independent candidate will require a completed reversal displacement /
MSS after the accepted pullback and source the target from causally active
completed-day or completed-week external liquidity beyond the local structure.
Its single control will remove only the aggressive-flow agreement from that
completed MSS, preserving the same structure, entry, invalidation, target,
risk, cost and hold.
