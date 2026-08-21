from __future__ import annotations
import argparse,json
from datetime import date,timedelta
from pathlib import Path
from event_episode_harvest import HarvestConfig,harvest
p=argparse.ArgumentParser();p.add_argument('--start',required=True);p.add_argument('--end',required=True);p.add_argument('--warmup-days',type=int,default=20);p.add_argument('--symbols',nargs='+',required=True);p.add_argument('--cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();s=date.fromisoformat(a.start);e=date.fromisoformat(a.end);print(json.dumps(harvest(HarvestConfig(s,e,s-timedelta(days=a.warmup_days),tuple(a.symbols),a.cache,a.output)),indent=2,sort_keys=True))
