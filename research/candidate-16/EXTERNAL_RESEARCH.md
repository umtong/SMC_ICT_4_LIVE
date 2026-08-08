# Candidate 16 external research audit

Research date: 2026-08-08 (Asia/Seoul)

This investigation was completed **before** inspecting the project-wide candidate branches. The purpose was to identify independently supported mechanisms that distinguish failed auction from genuine acceptance early enough to leave executable reward space.

## Exact query families

```text
order flow imbalance price impact depth Cont Kukanov Stoikov
queue imbalance predictive limit order book Gould Bonart
crypto limit order submissions cancellations price discovery
liquidation order book depth recovery crypto
failed auction acceptance absorption aggressive volume no price progress
Turtle Soup false breakout failure test Adam Grimes
auction market theory acceptance rejection time volume
opening range breakout stocks in play retest
crypto intraday momentum reversal liquidity jumps
transaction costs sign prediction trading profitability
NautilusTrader backtest bar execution ordering L1 L2 L3
open source limit order book imbalance microprice impact analytics
```

Variations using `bitcoin`, `limit order book`, `failed breakout`, `absorption`, `retest`, and `transaction costs` were also searched.

## Sources, extraction, and implementation decision

| Tier | Source | Extracted finding | Decision / code mapping |
|---|---|---|---|
| Primary | Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*, https://arxiv.org/abs/1011.6402 | Short-horizon price change relates more robustly to order-flow imbalance than raw volume; impact varies with depth. | **Adopted as mechanism.** Aggressive effort is kept separate from realised progress. `effort_result_router.py`, `strategy.py::_router_observation`. |
| Primary | Gould & Bonart, *Queue Imbalance as a One-Tick-Ahead Price Predictor*, https://arxiv.org/abs/1512.03492 | Queue imbalance has significant very-short-horizon predictive content, strongest in large-tick assets. | **Partially adopted.** Depth is supplementary state evidence, never a standalone direction signal; project historical depth is coarser than a full event LOB. |
| Primary | Cryptocurrency price-discovery search result, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4867599 | Search metadata indicated limit submissions and cancellations matter. Full text returned HTTP 403. | **Not used as proof.** It only raised search priority; accessible OFI work and project data contracts ground the rule. |
| Primary | Bysik & Ślepaczuk, *Machine Learning-Based Bitcoin Trading Under Transaction Costs*, https://arxiv.org/abs/2606.00060 | Naive sign strategies failed after costs; forecast magnitude relative to cost changed economic performance. | **Adopted as execution constraint.** A completed state is rejected unless a still-active liquidity objective offers configured net R after costs. Fallback targets are prohibited. |
| Official | NautilusTrader, *Backtesting*, https://nautilustrader.io/docs/latest/concepts/backtesting/ | Event ordering and adaptive OHLC ordering matter; bar simulation does not create event-level queue truth. | **Adopted as validity rule.** Existing Nautilus engine and causal feature timestamps are reused; first pass is explicitly bar screening. |
| Official | NautilusTrader repository, https://github.com/nautechsystems/nautilus_trader | Existing engine handles order lifecycle, fills, accounting, fees, margin, and liquidation. | **Reused.** No backtester, matching engine, or NAV simulator was created. |
| Practitioner | Raschke & Connors, *Street Smarts* / Turtle Soup | Prior extreme, prompt re-entry, invalidation beyond excursion. | **Concept only.** Performance claims are not evidence; Candidate 16 validates independently and uses the full parent excursion as failed-auction invalidation. |
| Practitioner | Adam Grimes failure-test / failed-breakout material | Probe beyond level followed by rapid failure can define precise invalidation. The indexed page returned 404. | **Concept only.** Inaccessible material is not counted as validation. |
| Practitioner | Auction-market/order-flow acceptance, rejection, and absorption material | Rejection: effort without progress and rapid return. Acceptance: residence and continued progress outside value. | **Translated, not copied.** High effort + low progress + reclaim versus two completed outside closes + efficient progress. |
| Exploratory | Opening-range and “stocks in play” research | Breakout performance is context-dependent. | **Rejected as standalone setup.** Every break is not traded; `UNRESOLVED` is explicit. |
| Exploratory | Liquidation/depth recovery and crypto momentum/reversal papers | Depth can collapse around liquidation and post-event direction is liquidity-dependent. Some full texts were inaccessible. | **Falsification framing only.** A large candle or peer unanimity is not accepted as control transfer. |
| Open source | LOB imbalance/microprice/impact analytics repositories | Useful formulas exist, but analytics repositories do not prove alpha or realistic portfolio execution. | **Concept reused; engine rejected.** Existing project features and Nautilus remain authoritative. |

## Synthesis fixed before internal inspection

```text
important boundary interaction
  -> measure aggressive effort independently from price result
  -> observe completed boundary residence/reclaim
       -> high effort + low progress + fast reclaim: FAILED_AUCTION
       -> sustained outside closes + efficient progress: ACCEPTANCE_CONTINUATION
       -> neither: UNRESOLVED / NO TRADE
  -> enter only while the same auction leg has natural objective space after costs
```

Evidence roles are separated:

- Context: already-confirmed active liquidity pool.
- Interaction: causal crossing with minimum penetration.
- State: effort relative to result and outside residence.
- Transition: completed reclaim or second efficient outside close.
- Entry: completed failure or first shallow acceptance retest.
- Invalidation: full parent excursion or failed boundary hold.
- Target: still-active liquidity pool only; no invented expansion.

## Explicit rejections

1. Every sweep/reclaim as reversal.
2. Displacement, peer agreement, or flow serving simultaneously as context, state, confirmation, and entry.
3. Confirmations that consume the target.
4. Directional accuracy without cost space.
5. Practitioner performance claims as proof.
6. A separate event engine or NAV simulator.
7. Coarse historical depth presented as queue-position truth.

This is an audit of how external research affected code, not a claim that any cited source proves Candidate 16 profitable.
