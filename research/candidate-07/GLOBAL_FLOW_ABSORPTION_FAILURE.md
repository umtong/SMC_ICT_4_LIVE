# Global 15-second flow-absorption — clean Week-1 failure

## Classification

`LOGIC_ERROR / DISCARDED`

An initial empty-signal replay error was isolated first. NautilusTrader rejects
an empty custom-data collection. The replay adapter was changed only to omit an
empty signal collection while still replaying bars, funding, account and NAV.
All 116 causal/data/state tests passed before the identical frozen BTC Week-1 was
rerun. No market rule, threshold, target, stop, risk, cost or period changed.

## Hypothesis

Every complete fifteen-second auction was eligible. A reversal required extreme
one-sided aggressive quote flow, high total quote activity, meaningful range
expansion, weak directional price efficiency and a close back through the event
VWAP. A completed opposite-flow recovery confirmed the direction. The baseline
then waited for the first event-VWAP retest rejection; the single ablation
removed only that retest and entered on the recovery close.

Both variants used NautilusTrader `BacktestEngine`, current full-NAV 3% planned
loss sizing, taker fees, adverse ticks, funding reserve, one portfolio slot and
market-if-touched structural targets.

## Frozen BTC Week-1 result

Period: `2025-12-22` through `2025-12-29` exclusive.

| Measure | Baseline retest | Ablation: recovery close |
|---|---:|---:|
| Failed-aggression events | 14 | 14 |
| Opposite recovery confirmations | 6 | 6 |
| Entry-ready signals | 0 | 6 |
| Active days | 0 | 5 |
| Wins / losses | 0 / 0 | 2 / 4 |
| Win rate | — | 33.33% |
| Net return | 0.00% | -5.1737% |
| Daily geometric growth | 0.00% | -0.7560% |
| Profit factor | 0.000 | 0.4902 |
| Maximum drawdown | 0.00% | 8.3114% |
| Largest winner share | 0.00% | 65.57% |

The ablation produced four stop losses and two wins. Its six trades were spread
over five days, so the failure was not a single-day sampling artifact. The four
losses included gross target geometries from roughly 1.56R to 6.28R; missing
reward was therefore not caused by choosing targets too close to entry.

## Primary failure cause

Extreme aggressive flow with weak contemporaneous price response identifies an
interaction, but does not by itself identify a completed reversal. Opposite-flow
recovery frequently occurred before the original attack-side inventory had been
structurally trapped. Waiting for a generic event-VWAP retest did not repair the
problem because none of the six recoveries produced the defined first retest.
Removing that confirmation admitted trades, but the resulting direction was
wrong in four of six cases.

This is a clean failure of the economic scenario, not an execution or fee-model
failure. Threshold relaxation or target changes would not add the missing causal
statement about *which pre-existing liquidity was swept and which protected
swing was subsequently broken*.

## Valid components retained

- every complete fifteen-second auction as an unbiased event population;
- past-only rolling flow, activity, imbalance and ATR references;
- exact no-trade seconds as zero-flow observations;
- signal delivery after the completed observation;
- no future path, MFE, MAE or terminal result in signals;
- cost-viable MIT targets and the unchanged 3% current-NAV risk contract;
- implementation-safe empty signal replay through NautilusTrader.

## Next independent hypothesis

The successor does not tune this candidate. It changes the causal event:

```text
causally confirmed 15-second swing liquidity
-> literal first touch and finite sweep with attack-side flow
-> completed reclaim inside the swept level
-> displacement close through the latest opposing protected local swing (MSS)
-> baseline: first rejection retest of the broken swing
-> ablation: MSS close
-> nearest unconsumed 15S / 1M / 5M opposing liquidity target
```

This adds the missing inventory-trap and protected-structure statements while
retaining the proven data, execution and risk contracts.

## Evidence

- source commit: `025f5ae1fabfe1e9577d0164629c7106351589f7`
- workflow run: `31176948110`
- artifact id: `8993253431`
- artifact SHA-256: `ee546ea7a12201249abe9ba823b3380234eaddf6b794ec00e37bb6f91ddfa96b`
