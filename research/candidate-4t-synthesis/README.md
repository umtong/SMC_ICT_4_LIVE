# Candidate 4t — counterfactual sequential auction ownership

Candidate 4t is a research synthesis, not a renamed threshold revision. It keeps
only mechanics already implemented and useful in the strongest preceding
branches, then completes the missing decision link between a liquidity event and
an account-level trade.

## Reused implementation

- `research_liquidity_auction_v7`: event-time hierarchical direction and fresh
  semantic-liquidity structure.
- `candidate-auction-episode-system`: the causal episode state sequence and
  price/volume response descriptors.
- `research_candidate_1k`: one exact first-live opposing-liquidity destination,
  structural invalidation, realistic first-passage labels, fees, one global
  account slot and 3% NAV risk sizing.
- `research_candidate_2c`: pending orders expire when the original event,
  destination or first-return opportunity dies, not because a timer elapsed.
- `research_liquidity_auction_v6`: a pending order may be replaced by a causally
  independent opportunity with greater account-time value; a filled position is
  immutable.

Depth/book features, ordinary `WAIT/ENTER/ABANDON`, exact semantic targets,
causal pending cancellation and sequential episode states already existed. They
are not claimed as new Missing Pieces.

## Missing connection completed here

A market-wide BTC/crypto impulse can occur at the same time as a local liquidity
interaction. Previous policies could count that coincidence as evidence that the
local auction owned the move. This produced the observed structural trade-off:
loose event policies traded often but failed badly, while strict late
control-transfer policies became too sparse.

Candidate 4t estimates three action-independent beliefs from development data:

1. full observable auction state;
2. local price/volume/structure state;
3. common-market-only state.

Positive full-model log-odds that can be explained by the common market alone are
removed. Independent local support is required, and the residual evidence is
accumulated through the event-time episode. Belief resets only when price and
volume contradict control; it never resets because a fixed number of bars passed.

Only after ownership is attributed are immutable entry, stop and exact target
priced. Separate models estimate fill, resolution, target-before-stop and account
occupation. The policy enters only when the resulting expected post-cost log
NAV growth is positive versus holding cash. There is no probability threshold,
score cutoff, fixed-R target, daily loss stop, forced time exit or fallback entry.

## Fixed account contract

One unchanged policy is applied to BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT. There
is one pending order or filled position for the entire account. Position quantity
makes a structural stop cost approximately 3% of current NAV; full NAV is the
margin base. The whole position exits only at its predeclared TP or SL. Same-side
cross-symbol cascades inside one four-minute causal market episode are counted and
routed once.

## Files

- `candidate_4t_harvest.py`: adapter that composes the existing exact-route,
  episode-state and causal-pending action universe.
- `candidate_4t_policy.py`: dependency-light counterfactual ownership models,
  sequential belief, action economics, one-slot routing and continuous NAV.
- `.github/workflows/research-candidate-4t-short.yml`: period-separated short
  diagnostic with development out-of-fold scoring and one untouched fresh window.

Short windows expose implementation and market-logic errors cheaply. They are not
presented as long-run performance evidence. The workflow commits actual scored
actions, orders, completed trades, replacements and `summary.json`; no PASS/FAIL
or promotion framework is generated.
