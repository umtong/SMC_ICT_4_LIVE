#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from datetime import date
from pathlib import Path
from confluence import run_research

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--start',type=date.fromisoformat,required=True)
    p.add_argument('--end',type=date.fromisoformat,required=True)
    p.add_argument('--warmup-days',type=int,default=20)
    p.add_argument('--symbols',nargs='+',required=True)
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    result=run_research(start=a.start,end=a.end,warmup_days=a.warmup_days,symbols=tuple(a.symbols),cache=a.cache,output=a.output)
    print(json.dumps(result,indent=2,sort_keys=True))
if __name__=='__main__':main()
