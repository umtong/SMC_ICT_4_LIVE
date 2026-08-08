# Candidate 16 v7 result

## Decision

`VALID SCREEN; ORIGINAL V52 COMPOSITION PRODUCED NO TRADE; DO NOT TREAT AS ECONOMIC REJECTION`

The registration-only repair successfully ran the original Candidate 05 v52
strategy in one NautilusTrader BacktestNode, one USDT account and one audited
global entry slot across BTC, ETH, SOL and XRP.

## Pre-registered evaluation

- build/warm-up: 2024-02-10 through 2024-02-18
- evaluation: 2024-02-12 through 2024-02-18
- global account integrity: pass
- same-timestamp peer observations: 0
- order rejections / denials / liquidations: 0 / 0 / 0
- maximum replayed entry intents plus positions: 1

## Funnel

- strictly-prior peer contexts: 40,257
- robust residual extremes: 1,169
- residual inflections: 17
- OI-not-expanding passes: 5
- local flow/depth passes: 1
- armed setups: 1
- entry submissions / positions: 0 / 0
- ending NAV: 100,000 USDT

## Structural finding

The only complete setup was XRP at 2024-02-15 05:28:59.999 UTC:

- residual: +4.0775 ATR units;
- robust z: +2.5232;
- side: short convergence;
- OI change 15m: -0.1702%;
- flow 15s / 60s / 3m: -0.5084 / -0.2825 / -0.1482;
- directional depth support: 0.08564.

The v52 state gate accepted this observation, then the inherited v26 pending
handler immediately reused the identical depth observation with a stricter 0.10
threshold and closed it on the same timestamp. The setup therefore never
received a strictly later confirmation. This is the circular evidence-role
problem warned about by the project instructions, not evidence that residual
convergence lacks economic value.

Using the persisted causal feature stream, the setup's approximate directional
VWAP movement was adverse during the first five minutes (-47.1 bp for the short)
but later became +34.7 bp at 30 minutes and +174.2 bp at 120 minutes. An immediate
fade would have been badly timed; a later independent convergence leg could have
been tradeable.

## Retained and changed

Retain unchanged:

- strictly prior peer observations;
- robust cross-sectional residual state;
- OI / local flow / depth state evidence;
- one shared account and final global slot;
- 3% current-NAV risk and NautilusTrader execution.

Change in the next candidate:

- do not pass an armed residual state into the inherited same-bar rejection
  confirmation;
- freeze it without an order;
- require a strictly later residual contraction, price reversal, aggressor flow
  and depth transition;
- treat that later transition as a new auction leg with its own extreme, FOK
  price cap and real liquidity objective.

- workflow run: `31256434262`
- artifact: `candidate-16-v7-screen-29bc91cffdeb67e322e6cfa9326725b847ea1bf5`
