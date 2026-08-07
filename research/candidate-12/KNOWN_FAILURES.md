# Known failure conditions

These conditions invalidate or materially weaken a result even when headline return is positive.

1. **Bar path ambiguity** — a one-minute bar does not reveal the exact high/low sequence.  Nautilus adaptive ordering is used, but any strategy whose outcome depends heavily on both stop and target occurring inside the same bar requires tick/depth validation.
2. **Flow proxy limitation** — taker-buy volume is not order-book event imbalance.  Queue changes, cancellations, replenishment, hidden size, and cross-venue flow are absent.
3. **Large-size impact** — L1/bar replay can fill the full quantity at one level.  Effective fees and adverse ticks reserve execution cost, but success at large NAV must be revalidated with depth/participation constraints rather than assumed scalable.
4. **Funding approximation** — short intraday holding periods and elevated effective rates reserve funding uncertainty, but actual historical funding updates are not yet replayed.
5. **Timestamp/data revisions** — a changed source archive SHA-256 makes a prior result non-identical and must fail comparison rather than silently replacing evidence.
6. **No live structural target** — a valid sweep/acceptance without another live liquidity pool in the trade direction is not a complete scenario and is skipped.
7. **Low-activity crossing** — thin, noisy crossings below the relative-volume floor are not interpreted as meaningful acceptance/rejection.
8. **Persistent one-way repricing** — rejection logic can fail when apparent absorption is only a pause before trend continuation; the internal MSS and stop beyond the probe are the causal invalidation.
9. **Choppy boundary oscillation** — acceptance logic can whipsaw when repeated closes outside do not lead to a clean retest/reacceleration; the confirmation window expires rather than chasing.
10. **Global portfolio contention** — independent single-symbol results do not prove the four-symbol portfolio.  The complete candidate must arbitrate simultaneous plans and keep pending entries plus positions at one globally.
11. **Selection/overfit** — W1–W3 are diagnostic only.  W4–W6 must remain untouched until logic is frozen, and long-period/multi-symbol evaluation follows only after the short gates pass.
12. **Target not met** — fewer than five closed trades per week, weak payoff/win rate, post-cost daily geometric growth below 1%, liquidation, risk-budget breach, or invalid event chronology forbids a success claim.
