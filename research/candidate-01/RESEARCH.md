# Research basis and design decisions

This note separates external evidence from candidate inference. None of the cited studies proves that this candidate earns money in crypto. They justify which state variables are economically defensible enough to test.

## Evidence reviewed

| Area | Source | What the source supports | Candidate decision |
|---|---|---|---|
| Order-flow imbalance | Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*, Journal of Financial Econometrics, DOI `10.1093/jjfinec/nbt003` | Short-horizon price changes are more robustly related to order-flow imbalance than raw trade volume in the studied equity books | Use signed aggressive quote flow as confirmation; do not treat volume alone as direction |
| Queue/volume imbalance | Gould & Bonart, *Queue Imbalance as a One-Tick-Ahead Price Predictor*, arXiv `1512.03492`; Cartea, Donnelly & Jaimungal, DOI `10.1080/1350486X.2018.1434009` | Book imbalance can predict the next price/order direction in the studied Nasdaq data and can reduce adverse selection | Adopt the causal idea, but not their equity-specific coefficients or passive limit execution |
| Event clustering and adverse selection | Cartea, Jaimungal & Ricci, *Algorithmic Trading, Stochastic Control, and Mutually Exciting Processes*, DOI `10.1137/18M1176968` | Market-order arrivals and short-term alpha can cluster; ignoring adverse-selection state is costly | Require displacement plus flow, impose finite state expiry, and avoid assuming every touched limit fills |
| Crypto intraday periodicity | Hansen, Kim & Kimbrough, *Periodicity in Cryptocurrency Volatility and Liquidity*, DOI `10.1093/jjfinec/nbac034`, arXiv `2109.12142` | BTC/ETH activity and volatility exhibit recurrent day/hour/within-hour structure across centralized venues | Use fixed UTC auction blocks as repeatable state boundaries; do not optimize named sessions or weekdays |
| Backtest/live event semantics | NautilusTrader v1.230.0 documentation and source | Bar execution order, contingent orders, fees, margin, liquidation, and shared event semantics are provided by the engine | Reuse the engine; candidate code owns no fill ledger or custom PnL implementation |
| Public SMC implementation audit | `joshyattridge/smart-money-concepts`, `smartmoneyconcepts/smc.py` | Popular convenience labels include forward shifts for FVG joining and symmetric future windows for swing highs/lows | Reject those labels for decision time; define every event by its observation timestamp |
| Public market data | Binance Vision USD-M monthly one-minute klines | OHLCV, quote volume, trade count, and taker-buy volume are available in deterministic archives | Use taker-buy quote share as a signed aggressive-flow proxy and hash every archive |

### Important transfer limit

Most microstructure findings above were established in equity limit-order books, often at much shorter horizons and with full depth. They are **motivation**, not evidence that the effect magnitude transfers to Binance perpetuals. Candidate 01 therefore uses only the directional causal proposition—aggressive-flow state matters—and requires out-of-sample crypto evaluation.

## Broad alternatives considered

### 1. Hindsight swing graph

A symmetric pivot can produce visually clean BOS/CHoCH labels, but the pivot is unknown until future bars arrive. Delaying the event until confirmation makes it valid, yet creates variable latency and often turns the label into a description rather than an actionable cause. Candidate 01 instead uses a completed fixed range for external structure and a trailing-only lookback for internal structure.

**Rejected:** future-confirmed pivots as trade-time structure.

### 2. Single-pattern SMC detector

A sweep, FVG, order block, or displacement in isolation appears frequently. Treating any one as an entry condition confuses an event with a scenario.

**Rejected:** Boolean pattern conjunctions without ordered state transitions.

### 3. Named-session folklore

London/New York/Asia labels may proxy for activity, but crypto trades continuously and the relevant participation pattern changes. Optimizing exact clock windows risks memorizing a venue regime.

**Adopted differently:** four-hour UTC blocks are mechanical auction anchors. No weekday, session name, or instrument-specific hour is a rule.

### 4. Passive FVG limit entry

A resting limit at an imbalance can improve headline reward/risk, but one-minute bars do not reveal queue position or available depth. Assuming every touch fills is structurally optimistic.

**Rejected:** touch-equals-fill passive entry. Candidate 01 waits for a completed retrace-rejection bar, then sends a delayed market bracket and charges taker-like costs.

### 5. Pure breakout or pure mean reversion

A boundary traversal can either fail or establish new value. A single unconditional response applies the wrong behavior to one of those states.

**Adopted:** mutually exclusive rejection and acceptance branches sharing the same range and risk grammar.

### 6. Indicator ensemble / model score

A weighted score can hide conflicting causal stories and encourages score-dependent risk multipliers.

**Rejected:** score-based entries and score-based size. A state either reaches a valid terminal transition or it does not.

### 7. VPIN, Hawkes estimation, and full depth models

These adjacent methods are useful when trade or book event data are available. One-minute aggregate klines are insufficient to estimate them faithfully.

**Deferred, not approximated:** no invented queue, depth, or event-arrival data.

## Why the exact state sequence is plausible

### Rejection branch

1. A completed range boundary is already visible to all participants.
2. Traversal can trigger stop orders and breakout participation.
3. Closing back inside means the traversal did not establish outside value.
4. Opposing displacement through trailing internal structure shows that response is not merely a wick.
5. Opposing aggressive flow provides an observable participation check.
6. A causal imbalance/retrace zone offers a later decision point.
7. Rejection of that retrace invalidates close to the sweep extreme and targets known opposing liquidity.

### Acceptance branch

1. The first outside close with same-direction flow is only provisional.
2. A second directional outside close reduces the chance of a one-bar stop run.
3. A retest that holds the old boundary demonstrates role change.
4. The target is a fixed fraction of the prior range width, not a hindsight swing.

## Parameter discipline

The parameter set is deliberately small and dimensionless where possible. Distances use prior ATR; activity and flow use trailing z-scores; structural lookbacks and phase expiries are fixed in minutes/bars. The same values apply to long/short and both early/late historical regimes.

The values in `config.json` were frozen before running the seeded week suite. Research may replace the candidate with a new coherent hypothesis, but a parameter change after seeing held-out results invalidates those weeks as confirmation data and must be recorded with a new candidate/version.

## Falsification questions

The diagnostics are designed to answer more than “did it make money?”:

- Did a boundary traversal become rejection or acceptance?
- Did displacement occur before the retrace?
- Did the delayed market entry destroy the planned reward/risk?
- Which branch, side, and state transition produced or failed to produce trades?
- Were losses caused by wrong direction, invalidation placement, execution costs, or time expiry?
- Did activity concentrate in one random week?
- Did effective leverage or maintenance margin make the apparent alpha non-deployable?

The full candidate is accepted only by the declared gate in `run_research.py`; all other outcomes are failure evidence.
