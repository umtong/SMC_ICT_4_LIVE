#!/usr/bin/env python3
"""Reproducible staged random-week screening for candidate-03."""
from __future__ import annotations
import argparse,csv,json,random,sys
from dataclasses import asdict,fields
from datetime import date,datetime,time,timedelta,timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
CANDIDATE_DIR=Path(__file__).resolve().parent;REPO_ROOT=CANDIDATE_DIR.parents[1];SRC=REPO_ROOT/'src'
if SRC.is_dir():sys.path.insert(0,str(SRC))
sys.path.insert(0,str(CANDIDATE_DIR))
from data_io import load_klines
from model import StrategyConfig
from strategy import Candidate03,NS_PER_DAY,NS_PER_MINUTE
from smc_ict_4.contracts import ResearchEvent
from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import create_run_manifest,write_json_atomic

SELECTION_SALT='candidate-03|liquidity-auction-v1|BTCUSDT'
UNIVERSE_START=date(2021,1,4);UNIVERSE_END=date(2025,12,22);MIN_WEEK_SEPARATION_DAYS=180

def deterministic_weeks(count:int=3)->list[date]:
    weeks=[];current=UNIVERSE_START
    while current<=UNIVERSE_END:weeks.append(current);current+=timedelta(days=7)
    seed=int.from_bytes(sha256(SELECTION_SALT.encode()).digest()[:8],'big');rng=random.Random(seed);pool=weeks[:];chosen=[]
    while pool and len(chosen)<count:
        week=rng.choice(pool);chosen.append(week);pool=[item for item in pool if abs((item-week).days)>=MIN_WEEK_SEPARATION_DAYS]
    if len(chosen)!=count:raise RuntimeError('selection universe cannot provide separated weeks')
    return chosen

def load_config(path:Path)->StrategyConfig:
    payload=json.loads(path.read_text(encoding='utf-8'));allowed={f.name for f in fields(StrategyConfig)}
    unknown=sorted(set(payload)-allowed)
    if unknown:raise ValueError(f'unknown config fields: {unknown}')
    config=StrategyConfig(**payload);config.validate();return config

def to_ns(day:date,at:time=time.min)->int:return int(datetime.combine(day,at,tzinfo=timezone.utc).timestamp()*1e9)
def json_safe(value:Any)->Any:
    if isinstance(value,float) and value in (float('inf'),float('-inf')):return 'Infinity' if value>0 else '-Infinity'
    if isinstance(value,dict):return {k:json_safe(v) for k,v in value.items()}
    if isinstance(value,list):return [json_safe(v) for v in value]
    return value

def write_trades(path:Path,rows:list[dict[str,Any]])->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    if not rows:path.write_text('',encoding='utf-8');return
    normalized=[]
    for row in rows:
        normalized.append({k:(v.value if hasattr(v,'value') else v) for k,v in row.items()})
    with path.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(normalized[0]));writer.writeheader();writer.writerows(normalized)

def run_week(config:StrategyConfig,week_start:date,data_paths:list[Path],output:Path,label:str)->dict[str,Any]:
    bars,quality=load_klines(data_paths);start=to_ns(week_start);end=to_ns(week_start+timedelta(days=7))
    required=start-config.warmup_minutes*NS_PER_MINUTE;selected=[b for b in bars if required<=b.close_time_ns<end]
    if not selected or selected[0].close_time_ns>start-NS_PER_DAY:raise ValueError('insufficient warm-up data')
    events:list[ResearchEvent]=[]
    def emit(*,scenario_id:str,event_type:str,event_time_ns:int,observed_time_ns:int,previous_state:str,next_state:str,
             reason_code:str,reference_price:float|None,details:dict[str,Any])->None:
        events.append(ResearchEvent(scenario_id=scenario_id,instrument_id=config.instrument_id,event_type=event_type,
                     event_time_ns=event_time_ns,observed_time_ns=observed_time_ns,previous_state=previous_state,
                     next_state=next_state,reason_code=reason_code,
                     reference_price=None if reference_price is None else format(reference_price,'.12g'),details=details))
    metrics=Candidate03(config,emit).run(selected,start,end);trades=metrics.pop('trades_detail')
    metrics.update({'label':label,'week_start_utc':week_start.isoformat(),'week_end_utc':(week_start+timedelta(days=7)).isoformat(),
                    'data_quality':asdict(quality),'selection':{'salt':SELECTION_SALT,
                    'universe_start':UNIVERSE_START.isoformat(),'universe_end':UNIVERSE_END.isoformat(),
                    'minimum_separation_days':MIN_WEEK_SEPARATION_DAYS,
                    'selected_weeks':[item.isoformat() for item in deterministic_weeks()]}})
    output.mkdir(parents=True,exist_ok=True);write_trades(output/'trades.csv',trades);write_events(output/'scenario_events.jsonl',events)
    write_json_atomic(output/'metrics.json',json_safe(metrics))
    run=create_run_manifest(run_id=label,candidate=config.candidate,config_path=CANDIDATE_DIR/'config.json',extra={
        'week_start_utc':week_start.isoformat(),'week_end_utc':(week_start+timedelta(days=7)).isoformat(),
        'data_files':list(quality.files),'data_sha256':list(quality.sha256),'selection_salt':SELECTION_SALT})
    write_json_atomic(output/'run.json',run);return metrics

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,nargs='+',required=True)
    parser.add_argument('--week-start',type=date.fromisoformat,required=True);parser.add_argument('--label',required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--config',type=Path,default=CANDIDATE_DIR/'config.json')
    args=parser.parse_args();weeks=deterministic_weeks()
    if args.week_start not in weeks:parser.error(f'week must be precommitted: {[w.isoformat() for w in weeks]}')
    metrics=run_week(load_config(args.config),args.week_start,args.data,args.output,args.label)
    keys=('week_start_utc','trades','win_rate','mean_net_r','net_return','daily_geometric_growth','max_drawdown','target_met')
    print(json.dumps(json_safe({k:metrics[k] for k in keys}),indent=2,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
