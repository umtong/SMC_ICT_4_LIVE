# Research synthesis provenance

This system is a synthesis of prior repository work, not a claim that familiar
SMC/ICT mechanisms were newly discovered here.  The audit covered all 224
available remote heads, including every existing numbered candidate head, the
ML families, EasyChart v2-v26/RE1, auction/coherent/structural families, and the
production/execution branches.  A newer branch name was treated as a clue, not
as proof that it contains every earlier improvement.

## Reused mechanisms

| Responsibility | Existing provenance | Use in this system |
|---|---|---|
| Causal pivots, sweep/reclaim/acceptance, later first return | `research/candidate-02` C02/C04 | public-liquidity event grammar; not a new Missing Piece |
| Persistent local-minus-common ownership | `research_candidate_4t` | episode ownership state |
| Direction source vs route obstacle | `research/candidate-liquidity-auction-v1/semantic_liquidity_full.py` (`3984182f`) | semantic role separation |
| Causal dynamic diagonal/channel boundaries | `research/candidate-liquidity-auction-v2/dynamic_boundaries.py` (`2810bc7b`); EasyChart v18/v19 trendline files | versioned structural liquidity; future-survival leakage excluded |
| Multi-scale direction, path efficiency, common factor and relative strength | `research/candidate-directional-liquidity-policy-v2/directional_context.py` (`ea5df7e`) | direction and active draw-on-liquidity context |
| Failed/accepted/initiative response completion | `research/candidate-skilled-liquidity-response-v1/auction_response.py` (`fa6e291`), `response_event_detection.py` (`05ac6de`) | price-volume response evidence |
| Event-time journeys and one interaction owner | `structural_auction_control_v5.py` (`1fc9c69`) | structural lifecycle instead of a fixed setup timeout |
| Same-episode accepted-auction failure into trap | structural-auction family | one episode transition, not a separate strategy |
| Ordered channel phase and episode-level stop geometry | EasyChart RE1 `easychart_re1_phase.py`, `easychart_re1_episode_geometry.py` | interaction geometry |
| Mature balance only after two-sided defense and traversal | EasyChart RE1 `easychart_re1_mature_balance.py` | horizontal structure ownership |
| OB/FVG as event-local location, not thesis owner | EasyChart RE1 `easychart_re1_contextual_5m_ob.py`, `natural_geometry.py` | entry location only |
| Destination-first target and fresh objective ownership | structural v4/v5, EasyChart v16/v21/RE1 objectives | target selected before RR; no farther-target shopping |
| OI reset, deleveraging vs fresh sponsorship | candidate-02 v158 (`6e8e7a1` frozen policy, `1b150d6` result parent) and candidate-06 OIDB/CIRB | optional inventory observation; sparse results are not accepted as proof |
| One four-market Nautilus account/global slot | candidates 05, 29, 35, 51 | native account topology |
| First fill cancels parent remainder; every raced fill receives protection | `research/candidate-10/c10_flow_parent_execution.py` | managed parent and per-fill reduce-only protection |
| Native TradeTick matching and pending invalidation | candidate-01 `nautilus_tick_*_plan_backtest.py` | execution precedent |
| Exact 3% structural sizing | directional-liquidity-v2 `risk_sizing.py` | current MTM NAV and planned entry-to-stop distance set quantity; costs remain additional realized economics; impossible size is rejected, never clipped |
| Historical funding source and settlement precedent | EasyChart v13 funding files plus Nautilus `SimulationModule` pattern | official funding/mark data settled in the native margin account |
| Counterfactual/full-local-common evidence | candidate 4t, C47/C51/C55/C57, ML2 | diagnostics and causal feature provenance, not live hindsight |

## Existing findings which are not renamed as Missing Pieces

- Direction, liquidity mapping, failed/accepted auction taxonomy, activity-time,
  first-return entry, OB/FVG location, destination-first RR, episode ownership,
  one global account, fixed 3% sizing, and Nautilus execution all existed before
  this synthesis.
- OI positioning reset, passive absorption, metaorder lifecycle/exhaustion,
  common-mode winner/loser separation, delayed retest, objective consumption,
  and missed-opportunity harvesting were already investigated by C02/C06,
  C47/C51/C55/C57/C60, and the ML branches.
- The unresolved research problem is empirical: one integrated causal episode
  completion/selection law must retain enough opportunities while separating
  genuine control transfer from mechanically completed patterns across regimes.
  Calling another detector, hard filter, classifier shell, execution wrapper,
  or validation gate the Missing Piece would contradict the branch evidence.

## Negative evidence retained

- The broad causal-route system produced 653 trades over 56 days, 25.6% wins,
  PF 0.561 and near-total NAV loss; mechanical pattern completion overtrades.
- Structural v4/v5 produced only a handful of trades and remained negative;
  stacking strict journey gates can become sparse without creating edge.
- EasyChart direct MTF OB, raw horizontals, immediate channel-edge reversal,
  generic prior-range extremes, static flow votes, ADX/Kaufman regimes, symbol
  ranking, and delayed replication failed or did not generalize.
- Candidate-02 v158's 15/15 development trades and candidate-06 short-window
  OI results are mechanism clues, not untouched proof; inactive weeks and later
  failures are retained in the interpretation.
- Candidate-2c and candidate-57 changed sign across periods.  Family labels and
  larger planned RR cannot substitute for causal direction quality.

## Execution provenance in the integrated source

- `nautilus_data.py`: official one-minute trade bars plus timestamp-identical
  taker/quote flow sidecar.
- `nautilus_backtest.py`: one native MARGIN/NETTING account for four perpetuals;
  no custom fill, PnL, NAV, order, or portfolio ledger.
- `nautilus_funding.py`: official funding/mark join and native account funding
  adjustment.
- `sizing.py`: whole-MTM-NAV 3% structural stop-risk sizing.
- `live.py`: candidate-10-style per-fill protection, one global slot, explicit
  post-acceptance episode claim, and causal restart overlay.
- `replay_runner.py`: canonical multi-root source discovery and the same native
  strategy for historical execution, reusing the C29/C35/Candidate05 topology.

Performance claims require source-bound real-data ledgers from the integrated
code.  Workflow success, synthetic rows, smoke tests, or a successful contract
check are never treated as alpha evidence.
