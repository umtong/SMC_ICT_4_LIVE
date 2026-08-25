# Research synthesis record — candidate ML-k

Source branch at synthesis start: `candidate-liquidity-episode-policy-v1`
(`6bed368fc581d1f043def0a9f4b37970648b8455`). Working branch:
`research_candidate_ML_k`.

## Branch evidence actually reused

The auction-v3 through v8 sequence supplied the causal event vocabulary,
destination-first planning, first-return execution, one-account routing and the
continuous NAV accounting machinery. The world-model and auction-episode work
showed that the useful unit is a finite liquidity episode, not an isolated OB,
FVG or breakout candle.

Candidate 1a demonstrated that simply making the episode policy executable was
not enough: the fresh sample produced 40 trades across 21 days, mean about
`-0.307R`, ending NAV about `0.676`. Candidate 2c exposed the important positive
fragment: its fresh sample produced 25 trades across 11 days, mean about
`+0.163R`, ending NAV about `1.093`, and the `FIRST_RETEST_FORMING` family was
materially stronger (about `+0.905R` mean) while failed-auction routes lost.
However, the same candidate still planned remote targets around 6R.

The causal-inventory-transfer branch then made the failure unmistakable: roughly
684 trades across 56 days, mean about `-0.363R`, nearly destroyed NAV, and mean
planned target near 9.2R. The event detector had become an overactive signal
factory and the selected destination was commonly outside the same causal
response. Structural/skilled-control branches moved to the opposite extreme,
frequently producing zero or one trade in a three-day diagnostic.

The EasyChart re1 and ML-a line contributed the execution detail that survived
repeated experiments: a source footprint is not an entry by itself; the first
source return must show a lower-frame response, acceptance/reclaim or engulfing,
and a later convenient return must not replace a failed first return. ML-a also
made fill/mitigation lifecycle and latency explicit. ML2/ML3 contributed
first-response/target-first labels, governed structural stops, endpoint state and
retention/mitigation state, but their broad end-to-end experiments did not yield
a stable integrated policy.

The production episode-policy branch supplied the reusable Nautilus execution,
public Binance repository, event store, model bundle and restart/reconciliation
contracts. Those engineering components are not reimplemented here.

## Synthesis decision

The missing piece is **reachable control transfer**:

> At an important inherited location, did the episode transfer control strongly
> enough that its nearest credible completion frontier is more likely to trade
> before structural invalidation?

This is narrower than direction prediction and more useful than classifying an
OB/FVG. Deterministic market logic creates one explicit plan. ML estimates only
fill and target-before-stop first-passage hazards from event-relative features.
A plan may occupy the one global slot only when its conservative, cost-adjusted
expected account log-growth is positive.

This resolves both recurring pathologies without another forest of gates:

- remote 6–9R destinations are represented as low reachability unless prior
  causal observations genuinely support them;
- first-return/mitigation plans with nearby inherited frontiers receive a fair
  opportunity instead of being suppressed by many independent confirmations;
- simultaneous symbols are compared by the same shared probability and account
  objective, never by symbol-specific rules;
- repeated entries from the same causal episode remain forbidden.

## Fixed non-alpha contract

The research does not optimize the user's fixed account rules. Stop risk is 3%
of current NAV by quantity; full NAV is the margin base; leverage follows from
stop distance; there is no scale-in/out, daily loss cap or trade-count cap; entry,
TP and SL are declared before entry; gross planned RR must be at least 1.0R; the
universe is BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT; only one global pending order
or position may exist.

## Current diagnostic

Workflow: `.github/workflows/research-candidate-ml-k-short-diagnostic.yml`.
It harvests two development windows followed by one fresh window, all four
symbols, using the existing causal episode/Nautilus research stack. The first
window supplies no trades to the model because no mature history exists. Labels
from an earlier observation enter training only after fill/cancel or TP/SL
resolution was observable before the next period begins. Full outputs are
uploaded as a GitHub Actions artifact and the resulting summary is to be committed
under this directory after inspection.
