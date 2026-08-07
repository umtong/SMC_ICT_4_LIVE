#!/usr/bin/env python3
"""Consume every externally visible pool on first causal access."""
from __future__ import annotations

from pathlib import Path

MARKER = "C11_MICRO_FIRST_TOUCH_ALL_STATES"


def apply(root: Path) -> int:
    path = root / "microstructure.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return 0
    old = '''        if self.active is not None:
            if self.active.phase == "PROBING":
                self._update_probe(bar, atr, return_rms, flow_rms)
            else:
                # C11_MICRO_POST_CLASSIFICATION_AGE: reclaim/retest expiry is
                # measured in completed seconds after classification as well.
                self.active.bars += 1
            plan = self._maybe_absorption_plan(bar, atr)
            if plan is not None:
                return plan
            return self._maybe_acceptance_plan(bar, atr)

        if bar.ts_ns < self.cooldown_until_ns or self.pending_plan_id is not None or self.position_open:
            return None
        pool = self._touch_pool(bar, atr)
        if pool is not None:
            self._start_event(bar, pool)
'''
    new = '''        # C11_MICRO_FIRST_TOUCH_ALL_STATES: every completed-bar access
        # consumes its live pool even while another event, order, or position is
        # active.  A touched level can never be recycled into a later setup.
        touched_pool = self._touch_pool(bar, atr)

        if self.active is not None:
            if self.active.phase == "PROBING":
                self._update_probe(bar, atr, return_rms, flow_rms)
            else:
                # C11_MICRO_POST_CLASSIFICATION_AGE: reclaim/retest expiry is
                # measured in completed seconds after classification as well.
                self.active.bars += 1
            plan = self._maybe_absorption_plan(bar, atr)
            if plan is not None:
                return plan
            return self._maybe_acceptance_plan(bar, atr)

        if bar.ts_ns < self.cooldown_until_ns or self.pending_plan_id is not None or self.position_open:
            return None
        if touched_pool is not None:
            self._start_event(bar, touched_pool)
'''
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"microstructure first-touch anchor count={count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    print(f"microstructure lifecycle fix applied: {apply(root)}")


if __name__ == "__main__":
    main()
