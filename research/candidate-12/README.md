# Candidate 12 — New-York raid of completed London buy-side liquidity

Candidate 12 now contains one executable scenario rather than a universal pattern detector.

1. Freeze the completed 06:00–12:00 UTC London dealing range.
2. On a weekday, observe the first New-York trade above the frozen London high.
3. Require a completed five-minute close back inside within three bars.
4. Wait one additional completed five-minute bar.
5. Place a 15-minute protected sell limit at the London high.
6. Invalidate beyond the observed raid extreme plus an ATR buffer.
7. Target the first discount-side structural objective inside the completed London range.

NautilusTrader owns fills, contingent orders, fees, margin, positions, and account NAV. Quantity is current whole-account NAV × 3% divided by the complete expected loss per unit. W1 is the only design gate; W2/W3 remain unused unless W1 passes frequency, win-rate, payoff, and post-cost geometric-growth requirements.
