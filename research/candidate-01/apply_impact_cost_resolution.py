#!/usr/bin/env python3
"""Patch the impact candidate to a cost-resolvable information horizon."""

from pathlib import Path

path = Path(__file__).resolve().parent / "impact_regime_probe.py"
text = path.read_text(encoding="utf-8")
replacements = (
    (
        "CLOCK_CALIBRATION_MINUTES = 1\n",
        "CLOCK_CALIBRATION_MINUTES = 20\n",
    ),
    (
        '''            if direction is Side.LONG:
                stop = boundary - 0.20 * atr
                target = boundary + structure_width
            else:
                stop = boundary + 0.20 * atr
                target = boundary - structure_width
''',
        '''            if direction is Side.LONG:
                # Outside value is invalid only when the complete initiative
                # pulse is reclaimed, not on sub-cost noise at the boundary.
                stop = pulse_low - 0.10 * atr
                target = boundary + structure_width
            else:
                stop = pulse_high + 0.10 * atr
                target = boundary - structure_width
''',
    ),
)
for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one match, found {count}: {old!r}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
