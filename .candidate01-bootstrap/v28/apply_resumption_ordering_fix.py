#!/usr/bin/env python3
"""Fix v28 to compare against the prior completed pullback extreme."""
from pathlib import Path

path = Path("research/candidate-01/impact_elasticity_resumption_v28_nautilus_week.py")
text = path.read_text(encoding="utf-8")
old = '''            assert setup.pullback_high is not None
            assert setup.pullback_low is not None
            setup.pullback_high = max(setup.pullback_high, float(feature.bar.high))
            setup.pullback_low = min(setup.pullback_low, float(feature.bar.low))
            adverse = setup.adverse_elasticity
            resumed = (
                aligned_z is not None
                and aligned_z > 0.0
                and aligned_change > 0.0
                and self._resumption_break(setup, feature)
                and event_elasticity is not None
            )
'''
new = '''            assert setup.pullback_high is not None
            assert setup.pullback_low is not None
            # Compare the completed resumption close with the pullback extreme
            # known before this event. Updating the pullback extreme first
            # would require a close above its own high (or below its own low).
            prior_pullback_high = float(setup.pullback_high)
            prior_pullback_low = float(setup.pullback_low)
            resumption_break = (
                float(feature.bar.close) > prior_pullback_high
                if setup.side is Side.LONG
                else float(feature.bar.close) < prior_pullback_low
            )
            adverse = setup.adverse_elasticity
            resumed = (
                aligned_z is not None
                and aligned_z > 0.0
                and aligned_change > 0.0
                and resumption_break
                and event_elasticity is not None
            )
'''
if old not in text:
    if "prior_pullback_high = float(setup.pullback_high)" in text:
        raise SystemExit(0)
    raise SystemExit("expected v28 resumption block not found")
text = text.replace(old, new, 1)
old_tail = '''            if resumed and not elasticity_ok:
                self.counts["resumption_impact_not_recovered"] += 1
            remaining.append(setup)
'''
new_tail = '''            if resumed and not elasticity_ok:
                self.counts["resumption_impact_not_recovered"] += 1
            # Events which did not complete resumption remain part of the
            # evolving pullback path for a later causal break.
            setup.pullback_high = max(prior_pullback_high, float(feature.bar.high))
            setup.pullback_low = min(prior_pullback_low, float(feature.bar.low))
            remaining.append(setup)
'''
if old_tail not in text:
    raise SystemExit("expected v28 resumption tail not found")
path.write_text(text.replace(old_tail, new_tail, 1), encoding="utf-8")
