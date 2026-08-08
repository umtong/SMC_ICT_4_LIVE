# Candidate 39 — Causal Auction State Router V2

Candidate 39 is a **non-scalping intraday** system for BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT. It observes one-minute data, but the causal event is a completed 15-minute auction followed by three completed response minutes. Positions may remain open for up to 240 minutes; safety exits are never delayed to manufacture holding time.

## External idea mining

The investigation began outside the project. `EXTERNAL_RESEARCH.md` records the inspected Telegram, Reddit and Korean-community material and separates adopted mechanisms from material deliberately rejected.

The retained ideas were:

- OI change is an event/state observation, not long/short direction by itself.
- Spot/perpetual or price/flow disagreement distinguishes position building from fragile leverage-led movement.
- A liquidation/OI contraction event is only a setup clock; reversal requires boundary reclaim and later opposite initiative.
- Broken resistance/support is useful only if value is accepted or a later retest holds; chasing an already-extended move consumes target space.

Signal-room screenshots, liquidation-map “magnets”, OI-direction shortcuts and one-to-five-minute scalping recipes were not used as trading rules.

## V1 falsification

The first fixed seven-day replay, 2026-07-08 through 2026-07-14, completed in one NautilusTrader account with realistic costs and no position-limit violation. It failed decisively:

| Metric | V1 result |
|---|---:|
| Starting NAV | 100,000 USDT |
| Ending NAV | 91,212.20 USDT |
| Total return | -8.7878% |
| Geometric daily growth | -1.3053% |
| Maximum drawdown | 8.9366% |
| Trades / wins | 3 / 0 |
| Profit factor | 0.0 |

The run exposed four structural defects rather than a threshold problem:

1. Two “continuations” retained only a small fraction of a 3.65–3.80 ATR wick excursion.
2. Event-opening flow could override contradictory flow at the actual decision minute.
3. Targets were evaluated with price-only R; one trade had only about 0.23 cost-after R.
4. A market parent filled on the next one-minute bar and made actual loss exceed the planned 3% budget.

This interval is now development data. V2 replays it only to verify that the identified false positives and execution defects are removed.

## V2 policy

### 1. Separate setup evidence from entry confirmation

- Interaction features are frozen at the first completed response minute.
- Entry confirmation is observed at the third completed response minute.
- The same observation cannot define the state and confirm its own entry.

### 2. Distinguish accepted value from a failed wick

A continuation requires at least half of the excursion beyond the completed range boundary to remain at the impulse close. A wick that gives back most of its excursion is routed to `UNRESOLVED`, even when OI and early aggressor flow look strong.

### 3. Use passive boundary-retest entries

Actionable states offer the completed range boundary as a LIMIT parent, valid for one additional 15-minute auction. No market-order chase is allowed. The inherited Nautilus contingent children remain responsible for target and stop execution.

### 4. Require cost-after target space

Before quantity is calculated, V2 applies entry/exit fees, adverse stop/target execution and the funding reserve. Net reward divided by planned per-unit loss must clear the existing state floor:

- continuation / peer repricing: 2.20R;
- cascade reversal: 1.60R.

A structural stop that is too wide is rejected. It is never pulled inward merely to improve reported R.

## Scenario families

1. **BUILD_ACCEPT_CONTINUATION** — coherent boundary break, OI build, current aligned flow, accepted value and passive retest.
2. **CASCADE_RECLAIM_REVERSAL** — sweep, OI contraction, frozen reclaim, later opposite initiative, current opposite flow and passive retest.
3. **PEER_LED_REPRICING** — leader-established direction and a lagging asset that coherently accepts its own boundary with unused target space.

All four symbols are routed at one common completed minute. One strongest coherent opportunity is selected; near-tied opposite opportunities produce no trade.

## Reused infrastructure

Candidate 35 remains the execution and accounting shell:

- checksum-verified Binance inputs;
- one NautilusTrader `BacktestNode`;
- one strategy process and one continuous margin account;
- global one-entry/one-position arbitration;
- fee, latency, slippage, funding and liquidation models;
- current-NAV 3% planned-loss sizing;
- manifests, reports and daily NAV accounting.

Candidate 39 does not implement a custom matching, account or portfolio simulator.

## Execution integrity

- Any order rejection while risk is live cancels residual orders and immediately flattens.
- A position is immediately flattened if its planned stop was already crossed on the entry bar.
- A passive parent expires after one 15-minute auction without a fill.
- The global four-symbol pending/open-position maximum remains one.

## Reproduction

```bash
smc4 doctor
python -m compileall -q research/candidate-39
python research/candidate-39/run.py \
  --config research/candidate-39/config.json \
  --start 2026-07-08 \
  --end 2026-07-14 \
  --cache .cache/c39-v2-development \
  --workspace .cache/c39-v2-development-work \
  --output artifacts/c39-v2-development
```

The 2026-07-08 through 2026-07-14 replay is a development falsification check only. After the V2 structure is stable, evidence must move to a later untouched interval and eventually to a long four-symbol continuous account. Project success is not claimed by this document.
