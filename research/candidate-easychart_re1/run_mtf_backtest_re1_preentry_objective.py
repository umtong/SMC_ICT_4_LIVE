#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
from easychart_re1_preentry_objective import PREENTRY_OBJECTIVE_REFRESH_RULE, EasyChartRE1PreEntryObjectiveBundle
import run_mtf_backtest_re1_flow as _flow_runner
_flow_runner._runner.EasyChartRE1NaturalBundle = EasyChartRE1PreEntryObjectiveBundle

def _rewrite(output: Path) -> None:
    for name in ("metrics.json","run.json"):
        p=output/name
        if not p.exists(): continue
        x=json.loads(p.read_text(encoding="utf-8")); x.update({"candidate":"candidate-easychart_re1_preentry_objective","target_rule":PREENTRY_OBJECTIVE_REFRESH_RULE})
        p.write_text(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
if __name__=="__main__":
    out=_flow_runner._output_path(sys.argv); _flow_runner._runner.main()
    if out is not None: _flow_runner._rewrite_metadata(out); _rewrite(out)
