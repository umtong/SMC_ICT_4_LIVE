#!/usr/bin/env python3
"""Retire the pre-entry semantic-rejection watch after repeated zero-fill evidence.

Candidates 19 through 23 evaluated the state in progressively corrected forms:

* inside-origin and post-stop origins were separated;
* two-close acceptance was made executable after costs;
* the unchanged AAC cross-market gate remained mandatory; and
* the structurally negative moderate-countertrend FAR route was reclassified.

Across Candidate 22, 67 complete rejected-FAR watches produced 17 acceptance
confirmations and nine costed plans, but every plan failed the independent
cross-market gate and no position filled. While alive, the watch also occupies
the local scenario sentinel and prevents detection of a later independent
auction on the same instrument.

Candidate 24 therefore retires only this pre-entry watch. A rejected FAR remains
a terminal diagnosed event; it is not inverted and it does not reserve local
scenario state. Post-stop failed-FAR continuation, ordinary AAC, dominant-peer
FAR, contested-countertrend rejection, trend-resumption FAR, costs, exact 3%
current-NAV risk and the one global order/position slot remain unchanged.
"""
from __future__ import annotations

import argparse
from pathlib import Path

OLD = '''    context = semantic_rejected_far_context(plan, reason)\n    BASE_MARK_REJECTED(self, plan, ts_ns, reason, details)\n    if context is not None:\n        arm_semantic_rejected_far(self, plan, ts_ns, reason, details, context)\n\n\n'''

NEW = '''    context = semantic_rejected_far_context(plan, reason)\n    BASE_MARK_REJECTED(self, plan, ts_ns, reason, details)\n    if context is not None:\n        scenario_id = f"{plan.scenario_id}-REJECTED-FAR-{ts_ns}"\n        self._event(\n            scenario_id,\n            "SEMANTIC_REJECTED_FAR_WATCH_RETIRED",\n            plan.observed_ts_ns,\n            ts_ns,\n            "PLAN_REJECTED",\n            "TERMINAL",\n            reason,\n            plan.expected_entry,\n            {\n                "parent_scenario_id": plan.scenario_id,\n                "failed_boundary": context.boundary,\n                "original_far_direction": context.original_direction.value,\n                "watch_armed": False,\n                "retirement_evidence": "C19_C20_C21_C22_ZERO_NATIVE_FILLS",\n                **(details or {}),\n            },\n        )\n        self.skips["SEMANTIC_REJECTED_FAR_WATCH_RETIRED"] += 1\n\n\n'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if "SEMANTIC_REJECTED_FAR_WATCH_RETIRED" in source:
        return False
    if "def semantic_rejected_far_context(" not in source:
        raise RuntimeError("Candidate 19 semantic-rejection state must be installed first")
    if source.count(OLD) != 1:
        raise RuntimeError(f"expected one semantic watch arm block, found {source.count(OLD)}")
    path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_state", type=Path)
    args = parser.parse_args()
    print(f"candidate24 retire semantic watch applied={apply(args.candidate_state)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
