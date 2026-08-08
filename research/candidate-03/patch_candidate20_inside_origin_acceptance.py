#!/usr/bin/env python3
"""Distinguish pre-entry inside-origin watches from post-stop outside-origin watches.

A failed-FAR state created by an actual sweep-extreme stop begins after price has
already crossed the failed boundary. Re-entry inside before two accepted closes
is therefore a valid immediate failure.

A semantic-rejection watch begins at the completed FAR reclaim, while price is
still inside the boundary by construction. Treating that initial location as a
failed continuation made every Candidate 19 watch terminal on its next bar.
Candidate 20 keeps the full Candidate 17 confirmation contract but permits an
inside-origin watch to wait for its first outside close. Once one outside close
has occurred, any deep re-entry again invalidates the state exactly as before.
"""
from __future__ import annotations

import argparse
from pathlib import Path

STATE_FIELD_ANCHOR = '''    original_target: float\n    state: str = "WAIT_ACCEPTANCE"\n'''
STATE_FIELD_REPLACEMENT = '''    original_target: float\n    origin_kind: str = "POST_STOP"\n    state: str = "WAIT_ACCEPTANCE"\n'''

DEEP_REENTRY_ANCHOR = '''def deep_reentry(state: FailedFarState, bar: BarObs, atr: float, retest_atr: float) -> bool:\n    allowance = retest_atr * atr\n    return (\n        bar.close < state.boundary - allowance\n        if state.direction == Direction.LONG\n        else bar.close > state.boundary + allowance\n    )\n\n\n'''
DEEP_REENTRY_REPLACEMENT = '''def deep_reentry(state: FailedFarState, bar: BarObs, atr: float, retest_atr: float) -> bool:\n    allowance = retest_atr * atr\n    return (\n        bar.close < state.boundary - allowance\n        if state.direction == Direction.LONG\n        else bar.close > state.boundary + allowance\n    )\n\n\ndef deep_reentry_is_terminal(\n    state: FailedFarState,\n    bar: BarObs,\n    atr: float,\n    retest_atr: float,\n) -> bool:\n    \"\"\"Return whether an inside close invalidates the current origin state.\"\"\"\n    if not deep_reentry(state, bar, atr, retest_atr):\n        return False\n    if state.origin_kind != "SEMANTIC_REJECTION":\n        return True\n    if state.state != "WAIT_ACCEPTANCE":\n        return True\n    # An inside-origin watch is neutral until price first demonstrates an\n    # outside close. After that first attempt, deep re-entry is a failed auction.\n    return state.outside_streak > 0\n\n\n'''

STEP_ANCHOR = '''    if deep_reentry(self, state, bar, atr, self.config.acceptance_retest_atr):\n        _terminal(self, state, bar, "FAILED_FAR_ACCEPTANCE_REJECTED")\n        return None\n'''
STEP_REPLACEMENT = '''    if deep_reentry_is_terminal(\n        state,\n        bar,\n        atr,\n        self.config.acceptance_retest_atr,\n    ):\n        _terminal(self, state, bar, "FAILED_FAR_ACCEPTANCE_REJECTED")\n        return None\n'''

SEMANTIC_STATE_ANCHOR = '''        original_entry=context.original_entry,\n        original_stop=context.original_stop,\n        original_target=context.original_target,\n    )\n    self._candidate16_failed_far_state = state\n    self._event(\n        scenario_id,\n        "SEMANTIC_REJECTED_FAR_CONTINUATION_ARMED",\n'''
SEMANTIC_STATE_REPLACEMENT = '''        original_entry=context.original_entry,\n        original_stop=context.original_stop,\n        original_target=context.original_target,\n        origin_kind="SEMANTIC_REJECTION",\n    )\n    self._candidate16_failed_far_state = state\n    self._event(\n        scenario_id,\n        "SEMANTIC_REJECTED_FAR_CONTINUATION_ARMED",\n'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if "def deep_reentry_is_terminal(" in source:
        return False
    if "def semantic_rejected_far_context(" not in source:
        raise RuntimeError("Candidate 19 semantic rejection patch must be installed first")
    replacements = (
        (STATE_FIELD_ANCHOR, STATE_FIELD_REPLACEMENT, "origin field"),
        (DEEP_REENTRY_ANCHOR, DEEP_REENTRY_REPLACEMENT, "deep reentry helper"),
        (STEP_ANCHOR, STEP_REPLACEMENT, "state step"),
        (SEMANTIC_STATE_ANCHOR, SEMANTIC_STATE_REPLACEMENT, "semantic state constructor"),
    )
    for old, _new, label in replacements:
        if source.count(old) != 1:
            raise RuntimeError(f"expected one {label} anchor, found {source.count(old)}")
    for old, new, _label in replacements:
        source = source.replace(old, new, 1)
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate20 inside-origin acceptance patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
