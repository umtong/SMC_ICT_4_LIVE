#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from pathlib import Path
from easychart_re1_flow_progress import ABSORPTION_MIDPOINT_PROGRESS_RULE,EasyChartRE1FlowProgressBundle
import run_mtf_backtest_re1_flow as _f
_f._runner.EasyChartRE1NaturalBundle=EasyChartRE1FlowProgressBundle
if __name__=='__main__':
 out=_f._output_path(sys.argv);_f._runner.main()
 if out:
  _f._rewrite_metadata(out)
  for n in ('metrics.json','run.json'):
   p=out/n
   if p.exists():
    x=json.loads(p.read_text());x.update({'candidate':'candidate-easychart_re1_flow_progress','rule':ABSORPTION_MIDPOINT_PROGRESS_RULE});p.write_text(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
