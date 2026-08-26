#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from pathlib import Path
from easychart_re1_confluence_flip_direct import CONFLUENCE_FIRST_EXACT_RETEST_RULE, EasyChartRE1DirectConfluenceBundle
import run_mtf_backtest_re1_flow as _flow_runner
_flow_runner._runner.EasyChartRE1NaturalBundle=EasyChartRE1DirectConfluenceBundle

def rw(out:Path):
 for n in ("metrics.json","run.json"):
  p=out/n
  if p.exists():
   x=json.loads(p.read_text());x.update({"candidate":"candidate-easychart_re1_confluence_flip_direct","confluence_retest_rule":CONFLUENCE_FIRST_EXACT_RETEST_RULE});p.write_text(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
if __name__=="__main__":
 out=_flow_runner._output_path(sys.argv);_flow_runner._runner.main()
 if out is not None:_flow_runner._rewrite_metadata(out);rw(out)
