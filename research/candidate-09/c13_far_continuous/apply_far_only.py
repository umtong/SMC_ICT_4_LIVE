#!/usr/bin/env python3
"""Single-variable Candidate 13 ablation: retain FAR and reject AAC.

All FAR detection, market-leadership approval, entry, stop, target, cost, risk,
portfolio arbitration, dates and data remain byte-identical to the frozen baseline.
"""
from pathlib import Path

path = Path(__file__).resolve().parent / "source" / "run_leadership_scdam.py"
text = path.read_text(encoding="utf-8")
if "FAR_ONLY_ABLATION" in text:
    raise SystemExit(0)
old = '''                if plan is None:
                    continue
                if ts_ns < self.config.evaluation_start_ns:
'''
new = '''                if plan is None:
                    continue
                if plan.scenario.value != "FAR":
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        "FAR_ONLY_ABLATION",
                        {"removed_scenario": plan.scenario.value},
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "FAR_ONLY_ABLATION_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "scenario": plan.scenario.value,
                        "reason": "FAR_ONLY_ABLATION",
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                if ts_ns < self.config.evaluation_start_ns:
'''
if old not in text:
    raise RuntimeError("Candidate 13 plan-routing contract not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
