"""Export deterministic one-by-one chart cases from the event-policy universe.

This is diagnostic research, not a scorecard.  It deliberately includes both the
strongest-looking winners and strongest-looking failures so market-logic errors can be
seen directly rather than inferred from aggregate statistics.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import hashlib

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from PIL import Image, ImageDraw

from data_re1_flow import load_range_flow
from event_episode_harvest import (
    HarvestConfig,
    _prepare,
    _detect_at,
    harvest,
    TICK_SIZE,
)
from metrics_state import load_range_metrics


def _robust_z(s: pd.Series) -> pd.Series:
    med=s.median()
    mad=(s-med).abs().median()
    return (s-med)/(1.4826*mad+1e-12)


def _case_quality(frame: pd.DataFrame) -> pd.Series:
    q=pd.Series(0.0,index=frame.index)
    reclaim=frame['style'].eq('RECLAIM')
    accepted=~reclaim
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
        g=g.copy()
        g['stable_hash']=g.event_id.map(_stable_hash)
        strongest=g.sort_values(['quality','stable_hash'],ascending=[False,True]).head(2)
        typical=(g.assign(risk_distance=(g.risk_bps-g.risk_bps.median()).abs())
                   .sort_values(['risk_distance','stable_hash']).head(2))
        chosen.extend([strongest,typical])
    out=pd.concat(chosen,ignore_index=True).drop_duplicates('event_id')
    # At most 16 per period; preserve both outcomes and both mechanisms.
    return out.sort_values(['style','outcome','quality'],ascending=[True,True,False]).head(16).reset_index(drop=True)


def _candles(ax: plt.Axes, bars: pd.DataFrame, width: float=0.65) -> None:
    for i,row in enumerate(bars.itertuples()):
        up=row.close>=row.open
        color='#168c6d' if up else '#c94b4b'
        ax.vlines(i,row.low,row.high,color=color,linewidth=.65)
        lo=min(row.open,row.close); h=max(abs(row.close-row.open),1e-12)
        ax.add_patch(Rectangle((i-width/2,lo),width,h,facecolor=color,edgecolor=color,linewidth=.4))
    ax.set_xlim(-1,len(bars))
    ax.grid(alpha=.16,linewidth=.5)


def _find_event(prepared: pd.DataFrame, decision_ns: int, style: str, side: str, symbol: str):
    ts=pd.Timestamp(decision_ns,unit='ns',tz='UTC')
    loc=prepared.index.get_indexer([ts])[0]
    if loc<0:
        raise RuntimeError(f'decision time not found {symbol} {ts}')
    matches=[x for x in _detect_at(prepared,loc,TICK_SIZE[symbol]) if x['style']==style and x['side']==side]
    if not matches:
        raise RuntimeError(f'event not reconstructed {symbol} {ts} {style} {side}')
    return loc,matches[0]


def chart_case(case: pd.Series, raw: pd.DataFrame, prepared: pd.DataFrame, output: Path, rank: int) -> dict:
    loc,event=_find_event(prepared,int(case.decision_time_ns),case['style'],case.side,case.symbol)
    decision=pd.Timestamp(int(case.decision_time_ns),unit='ns',tz='UTC')
    resolution=pd.Timestamp(int(case.resolution_time_ns),unit='ns',tz='UTC')
    indexed=raw.set_index('open_time_dt').sort_index()
    # raw timestamps are minute opens; the decision timestamp is completed-minute close.
    context=indexed.loc[decision-pd.Timedelta(hours=12):resolution+pd.Timedelta(hours=2)].copy()
    detail=indexed.loc[decision-pd.Timedelta(minutes=90):resolution+pd.Timedelta(minutes=35)].copy()
    context15=context.resample('15min',label='left',closed='left').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('quote_volume','sum')).dropna()

    fig=plt.figure(figsize=(15,9),dpi=140)
    gs=fig.add_gridspec(3,1,height_ratios=[2.35,2.35,.75],hspace=.08)
    ax1=fig.add_subplot(gs[0]); ax2=fig.add_subplot(gs[1]); ax3=fig.add_subplot(gs[2],sharex=ax2)
    _candles(ax1,context15)
    _candles(ax2,detail)
    ax3.bar(np.arange(len(detail)),detail.quote_volume.to_numpy(float),width=.75,color='#777777')
    ax3.grid(alpha=.15);ax3.set_ylabel('quote vol')

    boundary=float(event['boundary'])
    for ax,bars in ((ax1,context15),(ax2,detail)):
        ax.axhline(boundary,color='#7559a6',linewidth=1.2,label='pre-existing boundary')
        ax.axhline(case.entry,color='#2b6cb0',linewidth=1.0,label='entry')
        ax.axhline(case.stop,color='#b83232',linewidth=1.0,label='stop')
        ax.axhline(case.target,color='#2f855a',linewidth=1.0,label='target')
        if len(bars):
            idx=np.argmin(np.abs(bars.index.view('int64')-decision.value))
            ax.axvline(idx,color='#111111',linestyle='--',linewidth=.8)
    ax1.legend(loc='upper left',ncol=4,fontsize=8)
    ax1.set_title(
        f"{case.symbol} | {decision} | {case['style']} {case.side} | {case.boundary_families} | "
        f"{case.outcome} {case.net_r:+.2f}R | risk {case.risk_bps:.1f}bps | hold {int(case.holding_minutes)}m | quality {case.quality:+.2f}",
        fontsize=11,
    )
    ax1.set_ylabel('15m context');ax2.set_ylabel('1m detail')
    # sparse clock labels
    if len(detail):
        ticks=np.linspace(0,len(detail)-1,min(9,len(detail))).astype(int)
        ax3.set_xticks(ticks);ax3.set_xticklabels([detail.index[i].strftime('%m-%d\n%H:%M') for i in ticks],fontsize=8)
    name=f"{rank:02d}_{case.symbol}_{decision.strftime('%Y%m%d_%H%M')}_{case['style']}_{case.side}_{case.outcome}.png"
    path=output/name
    fig.savefig(path,bbox_inches='tight');plt.close(fig)
    return {'chart':name,'boundary':boundary,'event_extreme':event['event_extreme']}


def contact_sheet(paths: list[Path], output: Path) -> None:
    thumbs=[]
    for p in paths:
        im=Image.open(p).convert('RGB'); im.thumbnail((700,420)); thumbs.append((p.name,im.copy()))
    cols=2;cell_w=720;cell_h=455;rows=(len(thumbs)+cols-1)//cols
    sheet=Image.new('RGB',(cols*cell_w,rows*cell_h),'white');draw=ImageDraw.Draw(sheet)
    for i,(name,im) in enumerate(thumbs):
        x=(i%cols)*cell_w;y=(i//cols)*cell_h
        sheet.paste(im,(x,y+25));draw.text((x+5,y+5),name,fill='black')
    sheet.save(output,quality=92)


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    p.add_argument('--warmup-days',type=int,default=20);p.add_argument('--cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();start=date.fromisoformat(args.start);end=date.fromisoformat(args.end)
    args.output.mkdir(parents=True,exist_ok=True)
    harvest_dir=args.output/'harvest'
    harvest(HarvestConfig(start=start,end=end,load_start=start-timedelta(days=args.warmup_days),symbols=('BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT'),cache=args.cache,output=harvest_dir))
    events=pd.read_csv(harvest_dir/'events.csv.gz');actions=pd.read_csv(harvest_dir/'actions.csv.gz')
    cases=select_cases(events,actions)
    chart_dir=args.output/'charts';chart_dir.mkdir(exist_ok=True)
    extras=[];paths=[]
    for symbol,g in cases.groupby('symbol'):
        raw=load_range_flow(symbol,start-timedelta(days=args.warmup_days),end+timedelta(days=1),args.cache)
        metrics=load_range_metrics(symbol,start-timedelta(days=args.warmup_days),end+timedelta(days=1),args.cache)
        prepared=_prepare(symbol,raw,metrics)
        for idx,row in g.iterrows():
            extra=chart_case(row,raw,prepared,chart_dir,int(idx)+1);extra['event_id']=row.event_id;extras.append(extra);paths.append(chart_dir/extra['chart'])
    cases=cases.merge(pd.DataFrame(extras),on='event_id',how='left')
    cases.to_csv(args.output/'cases_manifest.csv',index=False)
    contact_sheet(sorted(paths),args.output/'contact_sheet.jpg')
    # Keep only compact outputs; full harvest already exists in the main workflow.
    import shutil
    shutil.rmtree(harvest_dir)
    print(cases[['event_id','style','side','outcome','net_r','quality','chart']].to_string(index=False))

if __name__=='__main__':
    main()
