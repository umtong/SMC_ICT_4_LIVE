# Candidate 12 — Completed London Range Auction

The candidate trades one completed 06:00–12:00 UTC London range through mutually exclusive auction outcomes rather than treating every sweep as a reversal.

- **Low rejection:** London closes in discount, New York raids the low, reclaims it, and the next completed bar holds inside; long to the opposite London boundary.
- **High rejection:** London closes at/above equilibrium or the reclaim itself expands by at least one ATR; short toward the discount-side structural objective.
- **High acceptance:** a weak high reclaim fails and a completed displacement bar closes above the raid extreme; long one London-range projection.
- **Low acceptance:** a deep-discount London low is accepted below its raid extreme; short one London-range projection.

All decisions use completed five-minute observations. NautilusTrader owns orders, fills, fees, margin, positions and account NAV. Quantity is current whole-account NAV × 3% divided by complete expected loss per unit. W2 is diagnostic because it informed this repair. I5 must pass W1 before the untouched W4 confirmation is allowed.
