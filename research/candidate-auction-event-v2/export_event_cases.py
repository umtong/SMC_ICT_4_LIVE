"""Export deterministic one-by-one chart cases from the event-policy universe.

This is diagnostic research, not a scorecard. It includes both the strongest-looking
winners and strongest-looking failures so market-logic errors are seen directly rather
than inferred from aggregate statistics. Rendering uses Pillow only so the pinned
research image does not need plotting dependencies.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import hashlib

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from data_re1_flow import load_range_flow
from event_episode_harvest import HarvestConfig, _prepare, _detect_at, harvest, TICK_SIZE
from metrics_state import load_range_metrics


def _robust_z(s: pd.Series) -> pd.Series:
    med=s.median(); mad=(s-med).abs().median()
    return (s-med)/(1.4826*mad+1e-12)


def _case_quality(frame: pd.DataFrame) -> pd.Series:
    q=pd.Series(0.0,index=frame.index)
    reclaim=frame['style'].eq('RECLAIM'); accepted=~reclaim
    q.loc[reclaim]=(
        _robust_z(frame.loc[reclaim,'event_penetration_bps'].clip(upper=frame.event_penetration_bps.quantile(.98)))
        +_robust_z(frame.loc[reclaim,'event_close_from_extreme_bps'].clip(upper=frame.event_close_from_extreme_bps.quantile(.98)))
        +0.5*_robust_z(frame.loc[reclaim,'activity_z_1d'])
        -0.35*_robust_z(frame.loc[reclaim,'impact_z_1d'])
        +0.25*_robust_z(frame.loc[reclaim,'boundary_count'])
    )
    q.loc[accepted]=(
        _robust_z(frame.loc[accepted,'event_break_body_bps'].clip(upper=frame.event_break_body_bps.quantile(.98)))
        +_robust_z(frame.loc[accepted,'event_hold_body_bps'].clip(upper=frame.event_hold_body_bps.quantile(.98)))
        +0.6*_robust_z(frame.loc[accepted,'event_break_return_signed_bps'])
        +0.4*_robust_z(frame.loc[accepted,'event_hold_return_signed_bps'])
        +0.35*_robust_z(frame.loc[accepted,'event_break_delta_signed'])
        +0.25*_robust_z(frame.loc[accepted,'boundary_count'])
    )
    return q.replace([np.inf,-np.inf],np.nan).fillna(0.0)


def _stable_hash(value: str) -> int:
    return int(hashlib.sha256(value.encode()).hexdigest()[:16],16)


def select_cases(events: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    actions=actions[actions.target_kind.eq('RR_1.5')].copy()
    cols=['event_id','symbol','decision_time_ns','style','side','boundary_family',
          'boundary_families','boundary_count','event_penetration_bps',
          'event_close_from_extreme_bps','event_break_body_bps','event_hold_body_bps',
          'event_break_return_signed_bps','event_hold_return_signed_bps',
          'event_break_delta_signed','event_hold_delta_signed','activity_z_1d','impact_z_1d']
    x=actions.merge(events[cols],on=['event_id','symbol','decision_time_ns','style','side'],how='left')
    x['quality']=_case_quality(x)
    chosen=[]
    for (style,outcome),g in x.groupby(['style','outcome']):
        if outcome not in ('TARGET_FIRST','STOP_FIRST','AMBIGUOUS_SAME_MINUTE'):
            continue
        g=g.copy(); g['stable_hash']=g.event_id.map(_stable_hash)
        strongest=g.sort_values(['quality','stable_hash'],ascending=[False,True]).head(2)
        typical=(g.assign(risk_distance=(g.risk_bps-g.risk_bps.median()).abs())
                   .sort_values(['risk_distance','stable_hash']).head(2))
        chosen.extend([strongest,typical])
    out=pd.concat(chosen,ignore_index=True).drop_duplicates('event_id')
    return out.sort_values(['style','outcome','quality'],ascending=[True,True,False]).head(16).reset_index(drop=True)


def _find_event(prepared: pd.DataFrame, decision_ns: int, style: str, side: str, symbol: str):
    ts=pd.Timestamp(decision_ns,unit='ns',tz='UTC')
    loc=prepared.index.get_indexer([ts])[0]
    if loc<0: raise RuntimeError(f'decision time not found {symbol} {ts}')
    matches=[x for x in _detect_at(prepared,loc,TICK_SIZE[symbol]) if x['style']==style and x['side']==side]
    if not matches: raise RuntimeError(f'event not reconstructed {symbol} {ts} {style} {side}')
    return loc,matches[0]


def _draw_panel(draw: ImageDraw.ImageDraw, bars: pd.DataFrame, box: tuple[int,int,int,int],
                levels: list[tuple[str,float,str]], decision: pd.Timestamp, title: str,
                volume: bool=False) -> None:
    left,top,right,bottom=box
    draw.rectangle(box,outline='#b8b8b8',width=1)
    draw.text((left+6,top+5),title,fill='black')
    plot_top=top+24; plot_bottom=bottom-(70 if volume else 18)
    if bars.empty: return
    vals=np.r_[bars.low.to_numpy(float),bars.high.to_numpy(float),np.array([v for _,v,_ in levels])]
    lo=float(np.nanmin(vals));hi=float(np.nanmax(vals));pad=max((hi-lo)*.05,abs(hi)*1e-6);lo-=pad;hi+=pad
    n=len(bars); span=max(right-left-20,1)
    def xx(i: int)->float:return left+10+(i+.5)*span/max(n,1)
    def yy(v: float)->float:return plot_bottom-(v-lo)/(hi-lo+1e-12)*(plot_bottom-plot_top)
    for frac in (.25,.5,.75):
        y=plot_top+frac*(plot_bottom-plot_top);draw.line((left+5,y,right-5,y),fill='#eeeeee',width=1)
    width=max(1,int(span/max(n,1)*.65))
    for i,row in enumerate(bars.itertuples()):
        color='#17866b' if row.close>=row.open else '#c34848';x=xx(i)
        draw.line((x,yy(row.low),x,yy(row.high)),fill=color,width=1)
        y1=yy(row.open);y2=yy(row.close);a=min(y1,y2);b=max(y1,y2)
        if b-a<1:b=a+1
        draw.rectangle((x-width/2,a,x+width/2,b),fill=color,outline=color)
    for name,value,color in levels:
        y=yy(value);draw.line((left+5,y,right-5,y),fill=color,width=2);draw.text((right-155,y-12),name,fill=color)
    idx=int(np.argmin(np.abs(bars.index.view('int64')-decision.value)))
    x=xx(idx);draw.line((x,plot_top,x,plot_bottom),fill='#222222',width=1)
    draw.text((x+3,plot_top+2),'decision',fill='#222222')
    if volume:
        vols=bars.quote_volume.to_numpy(float);mx=float(np.nanmax(vols)) if len(vols) else 1.0
        vtop=plot_bottom+10;vbottom=bottom-18
        for i,v in enumerate(vols):
            h=(v/max(mx,1e-12))*(vbottom-vtop);x=xx(i)
            draw.rectangle((x-width/2,vbottom-h,x+width/2,vbottom),fill='#888888')
        ticks=np.linspace(0,n-1,min(7,n)).astype(int)
        for i in ticks: draw.text((xx(int(i))-28,bottom-15),bars.index[int(i)].strftime('%m-%d %H:%M'),fill='#333333')


def chart_case(case: pd.Series, raw: pd.DataFrame, prepared: pd.DataFrame, output: Path, rank: int) -> dict:
    _,event=_find_event(prepared,int(case.decision_time_ns),case['style'],case.side,case.symbol)
    decision=pd.Timestamp(int(case.decision_time_ns),unit='ns',tz='UTC')
    resolution=pd.Timestamp(int(case.resolution_time_ns),unit='ns',tz='UTC')
    indexed=raw.set_index('open_time_dt').sort_index()
    context=indexed.loc[decision-pd.Timedelta(hours=12):resolution+pd.Timedelta(hours=2)].copy()
    detail=indexed.loc[decision-pd.Timedelta(minutes=90):resolution+pd.Timedelta(minutes=35)].copy()
    context15=context.resample('15min',label='left',closed='left').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),quote_volume=('quote_volume','sum')).dropna()
    boundary=float(event['boundary'])
    levels=[('boundary',boundary,'#7851a9'),('entry',float(case.entry),'#2563a6'),('stop',float(case.stop),'#b52d2d'),('target',float(case.target),'#277a4c')]
    image=Image.new('RGB',(1600,980),'white');draw=ImageDraw.Draw(image)
    header=(f"{case.symbol} | {decision} | {case['style']} {case.side} | {case.boundary_families} | "
            f"{case.outcome} {case.net_r:+.2f}R | risk {case.risk_bps:.1f}bps | hold {int(case.holding_minutes)}m | quality {case.quality:+.2f}")
    draw.text((12,8),header,fill='black')
    _draw_panel(draw,context15,(10,35,1590,450),levels,decision,'15-minute context')
    _draw_panel(draw,detail,(10,465,1590,970),levels,decision,'one-minute event path',volume=True)
    name=f"{rank:02d}_{case.symbol}_{decision.strftime('%Y%m%d_%H%M')}_{case['style']}_{case.side}_{case.outcome}.png"
    path=output/name;image.save(path)
    return {'chart':name,'boundary':boundary,'event_extreme':event['event_extreme']}


def contact_sheet(paths: list[Path], output: Path) -> None:
    thumbs=[]
    for p in paths:
        im=Image.open(p).convert('RGB');im.thumbnail((760,470));thumbs.append((p.name,im.copy()))
    cols=2;cell_w=790;cell_h=510;rows=(len(thumbs)+cols-1)//cols
    sheet=Image.new('RGB',(cols*cell_w,rows*cell_h),'white');draw=ImageDraw.Draw(sheet)
    for i,(name,im) in enumerate(thumbs):
        x=(i%cols)*cell_w;y=(i//cols)*cell_h
        sheet.paste(im,(x,y+25));draw.text((x+5,y+5),name,fill='black')
    sheet.save(output,quality=92)


def main() -> None:
    p=argparse.ArgumentParser();p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    p.add_argument('--warmup-days',type=int,default=20);p.add_argument('--cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();start=date.fromisoformat(args.start);end=date.fromisoformat(args.end);args.output.mkdir(parents=True,exist_ok=True)
    harvest_dir=args.output/'harvest'
    harvest(HarvestConfig(start=start,end=end,load_start=start-timedelta(days=args.warmup_days),symbols=('BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT'),cache=args.cache,output=harvest_dir))
    events=pd.read_csv(harvest_dir/'events.csv.gz');actions=pd.read_csv(harvest_dir/'actions.csv.gz');cases=select_cases(events,actions)
    chart_dir=args.output/'charts';chart_dir.mkdir(exist_ok=True);extras=[];paths=[]
    for symbol,g in cases.groupby('symbol'):
        raw=load_range_flow(symbol,start-timedelta(days=args.warmup_days),end+timedelta(days=1),args.cache)
        metrics=load_range_metrics(symbol,start-timedelta(days=args.warmup_days),end+timedelta(days=1),args.cache)
        prepared=_prepare(symbol,raw,metrics)
        for idx,row in g.iterrows():
            extra=chart_case(row,raw,prepared,chart_dir,int(idx)+1);extra['event_id']=row.event_id;extras.append(extra);paths.append(chart_dir/extra['chart'])
    cases=cases.merge(pd.DataFrame(extras),on='event_id',how='left');cases.to_csv(args.output/'cases_manifest.csv',index=False)
    contact_sheet(sorted(paths),args.output/'contact_sheet.jpg')
    import shutil;shutil.rmtree(harvest_dir)
    print(cases[['event_id','style','side','outcome','net_r','quality','chart']].to_string(index=False))

if __name__=='__main__':main()
