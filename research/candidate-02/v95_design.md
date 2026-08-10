# Candidate-02 v95 — Mature Defended Swing Breakout

## Research question

Can the partially successful v93/v94 common spot–perpetual accepted-breakout state become repeatable when the breached level has demonstrated causal market memory, rather than being a newly formed mechanical time boundary?

## Why this is a new structural hypothesis

v94 showed two separate facts:

1. selecting a distant pivot to satisfy a minimum RR caused one otherwise profitable eight-hour breakout to fail;
2. removing that target-skipping rule did not repair two four-hour-level losses, because those boundaries had not demonstrated that traders actually treated them as important.

v95 therefore changes the **source of liquidity authority**, not the result gate.  It removes four-hour, eight-hour and UTC-day clock boundaries.  A level must earn eligibility through a causal life-cycle.

## Detector layer

A fifteen-minute swing high or low is detected on completed bars.  It becomes known only after two later completed fifteen-minute bars confirm the pivot.  This layer emits candidate prices and timestamps; it does not trade.

## Liquidity-level life-cycle

```text
CONFIRMED_SWING
    ↓ survive 8h without first external breach
AGED_SWING
    ↓ revisit within 0.15 prior ATR
APPROACHED
    ↓ reject at least 0.25 prior ATR within 5 completed minutes
DEFENDED_MATURE_LEVEL
    ↓ first later external breach
CONSUMED_BREAK_EVENT
```

The level expires forty-eight hours after confirmation.  A breach consumes it even when later scenario confirmation fails, preventing repeated attempts against already harvested liquidity.

## Trading scenario state machine

```text
DEFENDED_MATURE_LEVEL
    ↓ first external breach with turnover at or above shifted prior median
BREACH_EVENT
    ↓ ≥2 outside closes in 3m + basis-adjusted spot participation + bounded basis expansion
COMMON_ACCEPTANCE
    ↓ same-side displacement + causal three-candle FVG
DELIVERY_IMBALANCE
    ↓ later gap touch closes beyond midpoint and old level
HELD_RETEST
    ↓ nearest intact confirmed 15m swing in delivery direction has positive cost-after RR
ENTRY
```

A move back 0.10 prior ATR inside the old level invalidates the retest.

## Target rule

The objective is always the nearest intact, already-confirmed fifteen-minute swing in the delivery direction.  It is never skipped to obtain a cosmetically larger reward/risk ratio.  If that nearest objective cannot pay the modeled costs, the scenario is a no-trade state.

## Risk and execution

- current NautilusTrader account NAV is the quantity basis;
- planned maximum loss is exactly 3% of NAV;
- expected entry and stop fees, slippage, market impact and funding are included in per-unit loss;
- no notional cap, discretionary leverage cap, score-based risk multiplier or custom backtest engine is used;
- NautilusTrader owns orders, fills, positions, fees, liquidation checks and NAV.

## Prospective variants

The central rule is fixed at an eight-hour maturity and twenty-minute retest window.  Before data collection, two adjacent retest windows (15/25 minutes) and two adjacent maturity horizons (6/10 hours) are registered.  They are robustness diagnostics, not a parameter search.

## Precommitted ablation

If the first week is not promotion eligible, the only core-variable ablation removes the prior defense-memory requirement while holding every other component fixed.  This directly tests whether market-memory evidence is informative or only suppresses frequency.
