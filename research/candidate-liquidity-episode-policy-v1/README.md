# Candidate Liquidity Episode Policy V1

This branch is the repository-resident restoration of
`research/candidate-liquidity-episode-policy-v1`. It is intentionally usable
without any chat attachment or local-only artifact.

## Restored trading grammar

```text
public liquidity and market structure
-> causal interaction episode
-> completed price/volume control evidence
-> one first-return entry origin
-> one structural invalidation
-> one pre-existing opposing destination
-> one pending order
-> one global account slot
```

The shared four-symbol policy represents three mechanisms:

- failed-auction reversal after a sweep and reclaim;
- accepted-auction continuation after outside acceptance and a held retest;
- initiative-mitigation continuation inside a still-live larger auction leg.

OB and FVG are entry-origin geometry, not standalone signals. Swing structure,
trend lines and channel boundaries are public liquidity and route context.
Fakeout/trap is the interaction episode. Price response, activity, signed flow,
cross-market breadth, relative return, basis and open-interest state are causal
control evidence.

## Structural contracts

- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT use the same policy.
- One causal episode may create at most one executable plan.
- The opposing destination is selected before reward/risk is calculated.
- Synthetic `CAUSAL_DEPARTURE_BAND` entries are rejected.
- A filled position exits only through its declared TP or SL.
- An unfilled order dies only with its original causal opportunity.
- Model labels enter training only after their fill/cancel or TP/SL result was observable.
- No heuristic probability fallback may trade when mature history is insufficient.
- All symbols share one pending/position slot and one continuous account path.

## Restoration provenance

The base router file had disappeared from the source branch tip even though
`route_episode_policy_causal.py` still imported it. The exact historical blob was restored:

- historical commit: `8ec7bbc6c6f29b0bae5b2d386106056ca8697d4e`
- restored `route_episode_policy.py` blob: `92459a08e98a634ec0a096ec1d567c78abdff7a9`

No replacement router was invented for the restoration.

## Windows 11 production-candidate runtime

The `production/`, `configs/` and `windows/` directories turn the restored policy into a
reproducible operational system without changing its alpha grammar.

```text
closed Binance USD-M futures / mark / index / public positioning data
-> restored episode_policy.generate_symbol
-> frozen causal model bundle (orders fail closed when absent)
-> account-wide arbitration and 3% NAV risk sizing
-> durable SQLite WAL event/hash/checkpoint state
-> no-order connected shadow
   or NautilusTrader live-data + sandbox paper execution
   or explicitly armed Binance USD-M Futures testnet execution
```

The public-data producer and Nautilus execution process communicate through one durable
SQLite database. An atomic account slot prevents two symbols or episodes from occupying
the account at the same time. Every decision is idempotent by episode/decision ID. Restart
recovery verifies both SQLite integrity and the append-only event hash chain before resuming.

Native Windows 11 commands, model construction, historical continuous reproduction,
shadow, paper and testnet procedures are documented in
[PRODUCTION_WINDOWS_11.md](PRODUCTION_WINDOWS_11.md).

The physical execution boundary is deliberate:

- `shadow`: refuses to start when Binance credentials are present and has no order gateway;
- `paper`: uses public live Binance data and NautilusTrader `SandboxExecutionClient`;
- `testnet`: requires a separate config, testnet credentials and an explicit PowerShell switch;
- no command in this candidate submits real-money Binance production orders.

## Reproduction

The restoration workflow remains:

```text
.github/workflows/candidate-liquidity-episode-policy-v1.yml
```

The production-candidate workflows add native Windows tests, a real Nautilus bracket
lifecycle smoke, bounded connected shadow restart/reconciliation and bounded Nautilus
sandbox connectivity. Long historical continuous evaluation and model-bundle construction
are separate explicit workflows because they download substantial public history.

Exact local/container commands and the original restoration evidence contract are in
[REPRODUCIBILITY.md](REPRODUCIBILITY.md).

## Evidence boundary

A green contract or operational workflow proves that the exact source can be installed,
started, restarted and reconciled on the tested environment. It does not turn a short smoke
into long-horizon alpha evidence. Long after-cost performance remains the output of the
single four-market continuous account, and connected shadow/paper quality remains the
observed operational record rather than the name of this branch.
