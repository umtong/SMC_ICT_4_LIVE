# Candidate 16 v6 28-day continuous-account result

## Decision

`VALID ECONOMIC FAILURE; ABANDON INFORMED-INITIATIVE CONTINUATION`

The first untouched week produced two wins, but the unchanged 28-day
continuous account did not reproduce that result.

## Pre-registered evaluation

- build/warm-up: 2024-02-16 through 2024-03-17
- evaluation: 2024-02-19 through 2024-03-17
- one continuous account, no weekly resets
- immutable L1 join coverage: 100%
- strategy-ready feature rows: 44,413

## Authoritative result

- cost-exceeding observations: 624
- informed initiatives: 196
- midpoint-held pullbacks: 157
- continuation confirmations: 25
- FOK entry submissions: 22
- actual closed positions: 16
- wins / losses: 4 / 12
- win rate: 25.0%
- starting NAV: 100,000 USDT
- ending NAV: 87,467.77764472 USDT
- total return: -12.53222235528%
- geometric daily growth: -0.47693434935%
- maximum drawdown: 17.2952501322%
- profit factor: 0.44192320779
- largest winner share: 37.05%
- liquidations: 0
- maximum simultaneous entry intents / positions: 1 / 1

Four order-rejection callbacks prevent using this run as pristine execution
certification. They arose in the inherited protective/child lifecycle after
positions were already filled and do not explain the economic failure: twelve
of sixteen actual positions lost, and most losing continuations failed within a
few minutes.

## Structural diagnosis

An event study on all 196 frozen initiatives found directional continuation for
only the next few minutes. From the later boundary-break confirmation, average
directional movement was approximately:

- +6.0 bp after one minute;
- +8.1 bp after five minutes;
- -26.5 bp after sixty minutes.

The model requires 20 bp of complete modeled round-trip friction before a state
can even qualify. Therefore the repeatable post-confirmation movement is too
small and too short-lived to be traded by this execution/cost model. Changing
stop, target or holding parameters cannot create an economic price interval
which is absent.

The useful elements retained are:

1. explicit nanosecond L1 alignment with fail-closed coverage;
2. state / transition / entry-role separation;
3. FOK all-or-none price-capped entry;
4. pullback-leg invalidation and pre-existing liquidity objectives;
5. the requirement that any next family exhibit a forward move substantially
   larger than total modeled friction before a full strategy is implemented.

- workflow run: `31255155470`
- artifact: `candidate-16-v6-28d-876be13ab21610d3c9fa461908a490c59134bf4a`
