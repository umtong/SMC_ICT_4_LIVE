#!/usr/bin/env python3
"""Single-variable Candidate 12 ablation: remove the LONDON_HIGH source pool.

All other detectors, state transitions, entries, invalidations, targets, costs, risk,
order handling and dates remain frozen. The source-level family was selected before
this ablation from the completed continuous baseline's largest loss contribution.
"""
from pathlib import Path

path = Path(__file__).resolve().parent / "source" / "strategy_adapter.py"
text = path.read_text(encoding="utf-8")
if "NO_LONDON_HIGH_SOURCE_ABLATION" in text:
    raise SystemExit(0)
old = '''            plan = self.logic.on_bar(observation, allow_entry=allow_entry)
            if plan is not None:
                self._submit_plan(plan)
'''
new = '''            plan = self.logic.on_bar(observation, allow_entry=allow_entry)
            if plan is not None:
                if plan.scenario.value.startswith("LONDON_HIGH_"):
                    self.logic.mark_plan_rejected(
                        plan,
                        self.last_ts_ns,
                        "NO_LONDON_HIGH_SOURCE_ABLATION",
                        {"removed_scenario": plan.scenario.value},
                    )
                else:
                    self._submit_plan(plan)
'''
if old not in text:
    raise RuntimeError("Candidate 12 on_bar submission contract not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
