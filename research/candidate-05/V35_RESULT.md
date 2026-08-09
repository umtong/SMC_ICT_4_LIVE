# Candidate 05 v35–v35c result — sequential aggressor-flow regime release

## Decision

**Discard the sequential-flow family as an active candidate.** The fixed
likelihood detector produced many directional state changes, but the subsequent
price/book release and first-retest chain did not create an executable trade in
the first frozen BTC week. The required single-variable ablation also produced
zero orders.

## Hypothesis

The detector compared a 2:1 directional aggressor regime with a 1:1 null using
fixed sequential likelihood ratios:

```text
p0 = 1/2
p1 = 2/3
alpha = beta = 5%
upper log-likelihood boundary = log(19)
```

A likelihood boundary was context, not an entry. It still required:

```text
prior evidence-range break
+ existing acceptance flow
+ price efficiency
+ activity burst
+ threatened-side depth withdrawal
→ first later structural-boundary touch
+ defended close
+ current tail flow
+ current directional depth
→ inherited v26 target, stop, fees, slippage, 3% NAV sizing and Nautilus order lifecycle
```

## Implementation-error separation

The first v35 implementation restarted each directional likelihood ratio at
zero after enough contrary evidence, but retained a price range accumulated
across older regimes. The statistical state and structural reference therefore
represented different episodes. v35b repaired only that inconsistency:
upward and downward evidence windows now reset with their own likelihood ratio.
Probabilities, likelihood boundary, release conditions, first-retest logic,
execution, costs and risk were unchanged.

The same frozen week was rerun. All fixed-environment, constructor, symmetry,
future-information, order and accounting tests passed.

## Authoritative evidence

### v35

Workflow `31144916426`, artifact `8981146995`.

- 7,363 informative completed minutes;
- 750 likelihood-boundary decisions;
- one complete flow/price/book release;
- one release watch;
- no defended first retest;
- zero submissions and zero incremental trades.

### v35b

Workflow `31146020710`, artifact `8981527267`, commit
`df5ff574e25a36f70fe49b6e6e51aff0b9461ad4`.

- 7,360 informative completed minutes;
- 967 directional decisions;
- 804 upward and 697 downward range restarts;
- two complete flow/price/book releases;
- two watches;
- one first-touch failure and one retest expiry;
- zero submissions and zero incremental trades.

v35b therefore repaired the episode definition and increased coherent release
observations from one to two, but did not produce an executable opportunity.

## Required one-variable ablation: v35c

v35c removed only the **current displayed-depth requirement at the first
retest**. It preserved the fixed likelihood model, directional evidence window,
release structure, activity, efficiency, breakout depth withdrawal, first-touch
finality, defended close, current tail flow, stop, target, costs, risk and
Nautilus execution.

Workflow `31146382874`, artifact `8981652515`, commit
`bebf8bb65f70109de43d6c4ffa93ac586eaac587`.

- two release confirmations and two watches were preserved;
- one actual first touch reached the ablation;
- that touch still failed the defended price/current-flow contract;
- the other watch expired without a retest;
- zero submissions and zero incremental trades.

The result rules out the explanation that a lagging displayed book alone blocked
v35b. Removing another confirmation after this result would progressively turn
the candidate into a bare range-break/retest pattern, contradicting its causal
premise.

## Useful observations retained

- Sequential likelihood evidence can be implemented causally and
  mirror-symmetrically without fitted scores.
- Direction-specific price ranges must restart with their corresponding
  likelihood state; otherwise the structural reference spans unrelated regimes.
- Frequent statistical state changes do not imply tradable price discovery.
  Only two of 967 decisions became complete flow/price/book releases.
- The absence of a defended first retest is a defined outcome, not permission to
  chase the release at market.

## Largest performance driver and next decision

The family failed before order submission. Stops, targets, leverage and execution
cannot repair a scenario whose first defended retest does not occur. The next
candidate changes the market cause rather than relaxing v35: intermarket SMT
non-confirmation at completed session liquidity must precede local reclaim,
order-flow response and CHoCH in one shared four-symbol account.
