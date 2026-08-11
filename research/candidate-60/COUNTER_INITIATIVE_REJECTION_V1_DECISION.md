# Candidate 60 — counter-initiative rejection V1 decision

## Status

**The exact immediate-rejection policy is retired. The causal mechanism remains under investigation. Policy-fresh data remain untouched.**

This is deliberately not a binary decision based on zero trades or the negative
peer-control result. The causal funnel separates state rarity, timing geometry,
and generic failed-initiative expectancy.

Evidence:

- GitHub Actions runs: `31483608282`, `31483960782`
- development: `2026-07-20` through `2026-07-26` UTC
- proposed completed trades: `0`
- peer-only raw completed events: `194`
- peer-only one-slot 15-minute events: `131`
- policy-fresh `2026-08-03` through `2026-08-09`: not consumed

## What zero proposed trades actually means

The exact V1 chain was:

```text
15m broad price-and-flow factor
→ strong leader
→ leader persists for the next 5m
→ a different asset launches a strong opposite 5m price-and-flow burst
→ the immediately following 1m turns price and flow back toward the factor
→ that same minute reclaims the counter-burst body midpoint
→ next-open entry
```

The outcome-blind causal funnel was:

| causal stage | count |
|---|---:|
| scored parent timestamps | 672 |
| broad price and flow direction aligned | 521 |
| strong parent leader exists | 357 |
| leader persists through the next 5m | 142 |
| strong opposite peer counter-burst exists | 9 |
| immediate peer price and flow both turn back | 3 |
| immediate full midpoint reclaim | 0 |

The mechanism was therefore not absent from the market. Nine strong
leader-owned counter-bursts occurred. The exact entry geometry failed because a
full rejection did not finish in one minute.

Without scoring any alternative return, the pre-declared ten-minute state-timing
diagnostic found first full rejection completion at delays:

- 2 minutes: 3 events;
- 3 minutes: 1 event;
- 5 minutes: 1 event;
- 6 minutes: 1 event;
- no completion within 10 minutes: 3 events.

Six of nine strong counter-bursts therefore did complete the intended state
transition, but only after more auction time. This is evidence against the
*immediate* transition assumption, not evidence that every version of the
failed-counter-initiative mechanism is wrong.

## What the peer-only control says

A generic policy requiring only:

```text
strong 5m single-asset price-and-flow burst
→ immediate 1m price-and-flow reversal
→ midpoint reclaim
```

was abundant but economically weak:

| horizon | one-slot events | mean gross bp | mean net bp |
|---:|---:|---:|---:|
| 5m | 154 | +0.4373 | -19.5627 |
| 15m | 131 | -1.0602 | -21.0602 |
| 30m | 115 | -2.5047 | -22.5047 |
| 60m | 82 | -9.5425 | -29.5425 |

The slightly positive five-minute gross mean is not accepted as useful alpha.
It is less than one basis point, only seven of 154 one-slot events exceed the
20 bp friction floor, and stronger event magnitude does not monotonically
improve the outcome. At the primary horizon every asset remains negative after
cost.

This control rules out a simple conclusion that every burst followed by a
one-minute midpoint reclaim should be faded. It also shows why zero proposed
trades must not be repaired by deleting the cross-asset state or lowering the
counter-burst threshold: doing so converges toward an abundant but cost-negative
generic pattern.

## Market-model correction

V1 assumed that a strong five-minute initiative would reveal failure within the
very next minute. The data show a two-stage process instead:

```text
strong counter-initiative
→ partial reaction / unresolved auction
→ some bursts regain acceptance and continue
→ others lose price impact over several completed observations
→ only then does a full price-and-flow rejection reclaim the burst body
```

The delay itself is economically meaningful. It represents the time needed for
local aggressive inventory to be absorbed, for the rest of the market to retain
or lose control, and for the counter-burst's marginal price impact to decay.

Therefore:

- the one-minute expiry is retired;
- the strong peer-burst requirement is preserved;
- the later price-and-flow rejection and midpoint reclaim are preserved;
- the cross-asset factor must still own the market when the delayed rejection
  completes;
- an unresolved burst must remain `NO TRADE` rather than be force-classified.

## V2 hypothesis authorized by the diagnosis

V2 is not a threshold relaxation. It uses a different temporal state machine on
a new development interval:

```text
broad common factor + strong leader
→ leader persists while a peer launches a strong opposite 5m burst
→ for at most two completed 5m auction lengths, remain UNRESOLVED
→ first later minute where:
     peer price turns toward the factor,
     peer aggressor flow turns toward the factor,
     counter-burst body midpoint is reclaimed,
     the other three assets still show common-factor ownership
→ enter at the next minute open toward the factor
```

The wait window is a state-expiry rule, not a holding-period optimization. It is
fixed at ten completed minutes because the outcome-blind funnel was explicitly
measured over two five-minute auction lengths. V2 must be tested on new
development data; V1 returns are not reused to choose V2 direction, symbols, or
holding horizon.

The exact opposite direction and a peer-only delayed-rejection control remain
mandatory. A positive aggregate V2 result will not be accepted if it is caused
by one trade, one symbol, one day, stale factor ownership, or a peer-only effect.
