# Candidate 57 — transient weak-reversion failure state machine v3

## Mechanism question

The 4h jump-reversion family has a genuine but rare large-winner engine. The broad completed-impulse stop preserved that engine better than the terminal-minute stop. Permanent break-even/trailing protection rescued several losses but destroyed the same large winners. The v3 question is narrower: can the system distinguish a weak reversion attempt that rolls back before confirmation from a strong reversion that escapes and should keep the original 4h horizon?

## Fixed causal state machine

- Source entry, source-score arbitration, completed 4h impulse stop, 3% all-in risk sizing and four-symbol single account remain unchanged.
- `ARM`: when net MFE first reaches a fixed R threshold, an all-in break-even floor becomes usable from the next completed minute.
- `ESCAPE`: if net MFE reaches a higher fixed R threshold before the active floor is crossed, the temporary floor is permanently disarmed and the original source horizon is preserved.
- If an already-active floor and escape are both touched in one minute, the old floor is evaluated first. This conservative ordering does not infer intrabar sequence.
- Once escaped, the protection cannot re-arm.

## Development variants fixed together

1. `impulse_control`: no transient protection.
2. `arm0p4_escape1p0`: arm at +0.4R, escape at +1.0R.
3. `arm0p5_escape1p0`: arm at +0.5R, escape at +1.0R.
4. `arm0p4_escape1p25`: arm at +0.4R, escape at +1.25R.

Interval: 2025-11-01 through 2025-11-14. This interval is already development data. Selection is based on exact episode preservation/rescue, gross winner and gross loss engines, occupancy effects and implementation validity—not on a binary scorecard.

A mechanism is eligible for one fresh short test only when it preserves the rare payoff engine and improves repeated weak-reversion failures. No long evaluation is authorized here.
