# Candidate 10 Research Operating System

This document turns the accumulated candidate-10 failures into a reusable research process. It is not a strategy description and does not relax the project target.

## 1. Objective and evidence standard

The project seeks a causal, executable day-trading system whose predetermined full-period automatic-account NAV achieves at least 1% geometric growth per evaluation day after all declared costs. One percent is a promotion threshold, not an optimization target or a performance cap.

A generation is never promoted because it:

- avoids trading;
- improves a loss without becoming positive;
- wins on one hand-selected event;
- reaches the target before realistic costs;
- produces a favorable aggregate result dominated by one market cascade;
- passes unit tests without a clean NautilusTrader replay.

## 2. Research order

Every new generation must be developed in this order.

1. **Causal market object** — identify the participant behavior being traded: liquidity collection, leverage clearing, absorption, acceptance, inventory transfer, or another economically coherent cause.
2. **Event identity** — define when two observations belong to the same underlying market event. One cause may create at most one scenario unless a genuinely new pool and new auction form later.
3. **Data availability contract** — state the event time, publication time, and first usable strategy time for every input. Ambiguous release semantics require a conservative lag sensitivity test before promotion.
4. **Detector** — detect the event without making a trading decision.
5. **Scenario state machine** — define formation, confirmation, entry-ready, invalidation, target, expiry, and terminal states in an explicit order.
6. **Execution lifecycle** — define parent submission, partial fills, cancellation, per-fill protection, stop/target triggers, scheduled flattening, and error recovery separately from the market state machine.
7. **Live cost ledger** — fees, expected slippage, size-dependent impact, and funding assumptions must affect the quantity calculation and every later NAV-based risk budget at the time they occur, not only in a post-run report.
8. **Causal tests** — prove that pivots, pools, targets, features, and auxiliary data were observable before the action that used them.
9. **First preselected BTC week** — run full and exact one-variable ablation in separate NautilusTrader processes under identical data, seed, costs, and risk.
10. **Decision** — fix implementation defects under controlled variables; otherwise classify the logic and either make one structural change or discard it.

## 3. Defect taxonomy

### 3.1 Implementation defect

An implementation defect means the intended scenario was not executed or measured as specified. Examples:

- corrupt source or data archive;
- future-time join;
- wrong quantity precision;
- parent remainder filling after protection failure;
- stop trigger using an unintended price type;
- modeled impact omitted from later NAV sizing;
- evidence state transitions describing callback order instead of market state.

Required response:

1. freeze week, data checksums, signal logic, parameters, seed, fees, risk, entry, stop, and target;
2. modify only the defective implementation component;
3. add a regression test reproducing the defect;
4. replay the same full and ablation variants;
5. do not interpret the pre-fix PnL as a strategy result.

### 3.2 Measurement defect

A measurement defect leaves orders intact but makes the verdict unreliable. Examples include post-hoc cost subtraction, coarse drawdown sampling, mislabeled gross PnL, or unreported event concentration.

It is handled like an implementation defect. The same trades must be remeasured before a logic decision.

### 3.3 Logic defect

A logic defect remains after a clean implementation replay. Examples:

- negative price expectancy before fees;
- one liquidation cascade split into many pseudo-independent trades;
- no cost-qualified target path;
- confirmation that removes every opportunity;
- alpha present only in the exact ablation;
- target or invalidation unrelated to the causal event.

Required response:

1. compare the exact core-variable ablation;
2. identify the dominant loss mechanism and any useful retained component;
3. make at most one structural causal change if a credible path exists;
4. otherwise discard the generation without threshold tuning, time exclusions, or hand-selected exceptions.

## 4. Event-identity rules

The following rules are mandatory after the v4 cascade failure.

- All same-side preexisting pools crossed by one completed event bar form one source-event cluster.
- A two-sided expansion is one ambiguous event, not two directional opportunities.
- Every pool touched by a nontradable true cross is consumed; it cannot later be relabeled as a fresh opportunity.
- One source cluster can reserve or create at most one active scenario.
- A target must have existed strictly before the source event began.
- Scenario independence is measured by source-event identity and time clustering, not by order count.
- Reports must show the largest 1-, 5-, and 15-minute event-cluster contribution to PnL.

## 5. Data-time rules

For every external series, the run manifest must record:

- provider and endpoint/archive;
- source checksum;
- raw timestamp field;
- timestamp interpretation;
- first strategy-observable timestamp;
- observation age distribution at use;
- future-time violation count;
- equal-timestamp ordering rule;
- known publication-delay uncertainty.

If an archive timestamp may denote the beginning rather than the publication/end of an interval, the main verdict cannot depend on same-timestamp access. A one-interval lag sensitivity is required before promotion, but it is a timing robustness test rather than the strategy's core-variable ablation.

## 6. Exact ablation contract

The full and ablation variants must share:

- market object and state sequence;
- source and target pools;
- data and timestamps;
- entry, stop, target, and expiry;
- costs and impact;
- whole-NAV 3% risk calculation;
- execution lifecycle;
- seed and evaluation period.

Exactly one causal variable is removed. If multiple features are removed or thresholds are retuned, the comparison is a new generation rather than an ablation.

## 7. Cost and risk rules

Planned loss per unit includes:

- executable entry-to-stop distance;
- entry and stop fees;
- expected entry and exit slippage;
- size-dependent market impact;
- funding when the scenario can span a funding event.

Quantity and impact are solved together. Expected costs are debited from a conservative NAV ledger when fills occur, and that ledger becomes the basis for every later 3% risk budget. No arbitrary nominal cap, strategy score multiplier, or unrelated leverage ceiling is added.

The report must preserve both:

- Nautilus engine NAV and commissions;
- conservative all-cost NAV used for promotion.

## 8. Promotion and discard gates

### First-week exploration gate

The first preselected BTC week is an efficient logic screen, not proof of the project target. Promotion requires at minimum:

- clean implementation and zero causal violations;
- enough distinct source events to estimate repeatability;
- positive conservative cost-after expectancy;
- more than a few wins from distinct source events;
- no single event cluster dominating profit;
- a recoverable drawdown path;
- full variant causally outperforming its exact ablation.

### Additional BTC weeks

Only a candidate passing the first screen runs the other preselected weeks. Failure in one week is diagnosed by state and event type rather than treated as a binary universal refutation, but a candidate with no structural explanation for the failure is discarded.

### Long BTC and transfer

Only a candidate surviving all short screens advances to continuous BTC evaluation. ETH, SOL, and XRP use the frozen market logic; only instrument metadata, causal normalization, fees, and observed liquidity costs may change.

The project target is declared only after predetermined long BTC and cross-instrument transfer evidence, not from a weekly gate.

## 9. Stop rules for inefficient research

Do not spend another generation on a candidate when any of these holds:

- gross price expectancy is materially negative with enough clean trades;
- the exact ablation is equal or better and the retained variable has no plausible nonlinear role;
- the system becomes zero-trade after confirmation is made causal;
- opportunity is too sparse and no larger market universe is permitted;
- target geometry is usually consumed by costs;
- one market event repeatedly recreates the signal;
- success requires period-specific thresholds or exclusions;
- the proposed change only alters risk, leverage, documentation, or fill optimism rather than alpha.

## 10. Cumulative learning ledger

| Generation | Main failure | Retained learning | Process improvement |
|---|---|---|---|
| v0 | market chasing plus narrow stops; costs overwhelmed positive pre-fee movement | raid/acceptance/rejection ordering carried some directional information | executable cost geometry must be present before order creation |
| v1 | generic fixed four-hour highs/lows were not durable liquidity coordinates | passive entry and cost-qualified target grammar | economic pool identity must precede time-block convenience |
| v2–v2.3 | causal structural confirmation reduced a losing fill but collapsed to zero opportunity | right-confirmed structure and retrace location versus scenario distinction | confirmation success is not trading-system success |
| v3–v3.2 | signed flow improved selection, but micro targets were too small and entries too sparse | executed flow and multi-scale destinations | separate trigger scale from destination scale |
| v4 | one cascade became many trades; event bars degenerated; gross expectancy was negative | same-side flow reduced damage; raw-tick protection worked | one-event-one-scenario identity and stress impact are mandatory |
| v20.0 | source archive was corrupt before strategy execution | immutable source manifest | source SHA and ZIP integrity are pre-execution gates |
| v20.1 | impact was initially a post-run adjustment, so later sizing could use optimistic NAV | square-root participation cost model | all modeled costs must enter the live conservative ledger |
| v20.2 | current controlled implementation under evaluation | pending clean evidence | no logic verdict until the identical-week implementation rerun completes |

## 11. Required terminal record

Every completed generation must leave:

- immutable source commit and data checksums;
- exact reproduction command or workflow run/job/artifact IDs;
- full and ablation metrics;
- scenario/event transition logs;
- trades linked to source pool/event identity;
- engine and conservative NAV curves;
- implementation, measurement, or logic classification;
- dominant failure driver;
- useful retained component;
- known failure conditions;
- explicit next action or discard decision.
