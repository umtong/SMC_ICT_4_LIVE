#!/usr/bin/env python3
"""Render every selected account trade as a self-contained SVG case."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from html import escape
from pathlib import Path
import math
import re

import numpy as np
import pandas as pd

from data_re1_flow import load_range_flow

W, H = 1680, 1120
LEFT, RIGHT = 90, 55
PLOT_W = W - LEFT - RIGHT
CONTEXT_Y, CONTEXT_H = 105, 320
DETAIL_Y, DETAIL_H = 465, 420
FLOW_Y, FLOW_H = 920, 135
UP, DOWN = "#16836d", "#c34a4a"
GRID, TEXT = "#d8dde3", "#17212c"
PERIOD_RE = re.compile(r"(?:dev|eval)-(\d{4})-([a-z]{3})")
MONTHS = {name: index for index, name in enumerate(("jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"), start=1)}


def _finite(value, default=0.0):
    try:
        value=float(value)
    except (TypeError,ValueError):
        return default
    return value if math.isfinite(value) else default


def _ts(value):
    return pd.Timestamp(int(value), unit="ns", tz="UTC")


def _period_dates(period: str) -> tuple[date,date]:
    match=PERIOD_RE.fullmatch(period)
    if not match:
        raise ValueError(period)
    start=date(int(match.group(1)), MONTHS[match.group(2)], 1)
    return start, start+timedelta(days=7)


def _x(index: pd.DatetimeIndex, timestamp: pd.Timestamp) -> float:
    if len(index)<=1:
        return LEFT
    position=int(index.searchsorted(timestamp, side="left"))
    position=min(max(position,0),len(index)-1)
    return LEFT+position/max(len(index)-1,1)*PLOT_W


def _yscale(low,high,y,height):
    span=max(high-low,1e-12)
    return lambda value: y+height-(float(value)-low)/span*height


def _candles(parts, frame, y, height, extra_prices=()):
    if frame.empty:
        return None
    valid=[float(v) for v in extra_prices if math.isfinite(_finite(v,float('nan')))]
    low=min([float(frame.low.min()),*valid]); high=max([float(frame.high.max()),*valid])
    pad=max((high-low)*.055,abs(high)*1e-6,1e-12);low-=pad;high+=pad
    sy=_yscale(low,high,y,height);n=len(frame);width=max(1.0,min(8.0,PLOT_W/max(n,1)*.68))
    for i,row in enumerate(frame.itertuples()):
        x=LEFT+(i+.5)/n*PLOT_W;color=UP if row.close>=row.open else DOWN
        parts.append(f'<line x1="{x:.2f}" y1="{sy(row.low):.2f}" x2="{x:.2f}" y2="{sy(row.high):.2f}" stroke="{color}" stroke-width="1"/>')
        top,bottom=sy(max(row.open,row.close)),sy(min(row.open,row.close))
        parts.append(f'<rect x="{x-width/2:.2f}" y="{top:.2f}" width="{width:.2f}" height="{max(1,bottom-top):.2f}" fill="{color}"/>')
    for fraction in (0,.25,.5,.75,1):
        py=y+height*(1-fraction);price=low+(high-low)*fraction
        parts.append(f'<line x1="{LEFT}" y1="{py:.1f}" x2="{W-RIGHT}" y2="{py:.1f}" stroke="{GRID}" stroke-width=".7"/>')
        parts.append(f'<text x="8" y="{py+4:.1f}" font-size="12" fill="{TEXT}">{price:.8g}</text>')
    return sy


def _hline(parts,sy,price,label,color,dash="",width=1.4):
    py=sy(price);dash_attr=f' stroke-dasharray="{dash}"' if dash else ""
    parts.append(f'<line x1="{LEFT}" y1="{py:.2f}" x2="{W-RIGHT}" y2="{py:.2f}" stroke="{color}" stroke-width="{width}"{dash_attr}/>')
    parts.append(f'<text x="{W-RIGHT-5}" y="{py-4:.2f}" font-size="12" text-anchor="end" fill="{color}">{escape(label)}</text>')


def _band(parts,sy,lower,upper,label,color,opacity=.12):
    y1,y2=sy(upper),sy(lower)
    parts.append(f'<rect x="{LEFT}" y="{min(y1,y2):.2f}" width="{PLOT_W}" height="{max(1,abs(y2-y1)):.2f}" fill="{color}" opacity="{opacity}"/>')
    parts.append(f'<text x="{LEFT+5}" y="{min(y1,y2)+14:.2f}" font-size="12" fill="{color}">{escape(label)}</text>')


def _vline(parts,index,timestamp,y,height,label,color):
    x=_x(index,timestamp)
    parts.append(f'<line x1="{x:.2f}" y1="{y}" x2="{x:.2f}" y2="{y+height}" stroke="{color}" stroke-width="1.1" stroke-dasharray="4 4"/>')
    parts.append(f'<text x="{x+3:.2f}" y="{y+14}" font-size="11" fill="{color}" transform="rotate(90 {x+3:.2f} {y+14})">{escape(label)}</text>')


def _flow(parts,frame):
    if frame.empty:return
    quote=frame.quote_volume.to_numpy(float);delta=(2*frame.taker_buy_quote_volume-frame.quote_volume).to_numpy(float)
    qscale=max(float(np.nanpercentile(quote,99)),1e-12);dscale=max(float(np.nanpercentile(np.abs(delta),99)),1e-12)
    n=len(frame);width=max(.8,PLOT_W/max(n,1)*.72);middle=FLOW_Y+FLOW_H*.58;zero=FLOW_Y+FLOW_H*.82
    for i,(volume,signed) in enumerate(zip(quote,delta)):
        x=LEFT+(i+.5)/n*PLOT_W;vh=min(volume/qscale,1.2)*FLOW_H*.48
        parts.append(f'<rect x="{x-width/2:.2f}" y="{middle-vh:.2f}" width="{width:.2f}" height="{vh:.2f}" fill="#7b8794" opacity=".45"/>')
        dh=max(-1.2,min(1.2,signed/dscale))*FLOW_H*.28;color=UP if signed>=0 else DOWN
        parts.append(f'<line x1="{x:.2f}" y1="{zero:.2f}" x2="{x:.2f}" y2="{zero-dh:.2f}" stroke="{color}" stroke-width="{max(1,width*.6):.2f}"/>')
    parts.append(f'<line x1="{LEFT}" y1="{zero}" x2="{W-RIGHT}" y2="{zero}" stroke="{GRID}"/>')
    parts.append(f'<text x="8" y="{FLOW_Y+16}" font-size="12" fill="{TEXT}">quote volume / aggressor delta</text>')


def render(row,raw,path):
    indexed=raw.copy();indexed.index=pd.DatetimeIndex(indexed.pop('open_time_dt'))+pd.Timedelta(minutes=1)
    event=_ts(row.diagnostic_event_time_ns);confirmation=_ts(row.diagnostic_confirmation_time_ns)
    departure=_ts(row.diagnostic_departure_time_ns);first_return=_ts(row.diagnostic_first_return_time_ns)
    response=_ts(row.diagnostic_response_time_ns);fill=_ts(row.fill_time_ns);resolution=_ts(row.resolution_time_ns)
    context=indexed.loc[event-pd.Timedelta(hours=12):resolution+pd.Timedelta(hours=2)].copy()
    context15=context.resample('15min',label='right',closed='right').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),quote_volume=('quote_volume','sum'),taker_buy_quote_volume=('taker_buy_quote_volume','sum')).dropna()
    detail=indexed.loc[event-pd.Timedelta(minutes=100):resolution+pd.Timedelta(minutes=40)].copy()
    prices=[row.source_lower,row.source_upper,row.diagnostic_zone_lower,row.diagnostic_zone_upper,row.actual_entry,row.stop,row.target,row.diagnostic_event_extreme,row.diagnostic_retest_extreme]
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">','<rect width="100%" height="100%" fill="#fbfcfe"/>']
    title=(f"{row.period} | {row.symbol} | {response.isoformat()} | {row.narrative_branch} {row.side} | {row.setup_kind}/{row.location_kind}/{row.response_kind} | {row.outcome} {row.net_r:+.2f}R")
    parts.append(f'<text x="{LEFT}" y="31" font-size="19" font-weight="700" fill="{TEXT}">{escape(title)}</text>')
    meta=(f"source {int(row.source_timeframe_minutes)}m {row.source_kind}; target {row.objective_kind}; gross RR {row.gross_rr:.2f}; risk {row.risk_bps:.1f}bps; hold {int(_finite(row.holding_minutes,0))}m; destination p {row.destination_probability:.3f}; action p {row.action_probability:.3f}; robust EV {row.robust_expected_r:+.3f}R")
    parts.append(f'<text x="{LEFT}" y="58" font-size="13" fill="#485564">{escape(meta)}</text>')
    sy1=_candles(parts,context15,CONTEXT_Y,CONTEXT_H,prices);sy2=_candles(parts,detail,DETAIL_Y,DETAIL_H,prices)
    for sy in (sy1,sy2):
        if sy is None:continue
        _band(parts,sy,row.source_lower,row.source_upper,'direction-owning external liquidity','#7652a7',.10)
        _band(parts,sy,row.diagnostic_zone_lower,row.diagnostic_zone_upper,'entry refinement zone','#d39522',.15)
        _hline(parts,sy,row.actual_entry,'actual next-open entry','#2365a7')
        _hline(parts,sy,row.stop,'structural invalidation / stop','#b82e36','5 3')
        _hline(parts,sy,row.target,'first opposing route obstacle / full exit','#25834f','5 3')
        _hline(parts,sy,row.diagnostic_event_extreme,'event extreme','#944b8c','2 4',1.0)
        _hline(parts,sy,row.diagnostic_retest_extreme,'retest extreme','#9b6c18','2 4',1.0)
    events=((event,'liquidity event','#7652a7'),(confirmation,'event classification','#d07a00'),(departure,'departure','#b66a00'),(first_return,'first return','#9b6c18'),(response,'completed response','#2365a7'),(fill,'fill','#1e3a8a'),(resolution,'exit','#111827'))
    for idx,y,height in ((context15.index,CONTEXT_Y,CONTEXT_H),(detail.index,DETAIL_Y,DETAIL_H)):
        for timestamp,label,color in events:_vline(parts,idx,timestamp,y,height,label,color)
    _flow(parts,detail)
    if len(detail):
        for i in np.linspace(0,len(detail)-1,min(9,len(detail))).astype(int):
            x=LEFT+(i+.5)/len(detail)*PLOT_W
            parts.append(f'<text x="{x:.1f}" y="{H-18}" font-size="11" text-anchor="middle" fill="{TEXT}">{detail.index[i].strftime("%m-%d %H:%M")}</text>')
    parts.append('</svg>');path.write_text('\n'.join(parts),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--result',type=Path,required=True);parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    frames=[]
    for filename in ('development_oof_account_trades.csv','evaluation_account_trades.csv'):
        path=args.result/filename
        if path.exists():
            frame=pd.read_csv(path)
            if not frame.empty:frames.append(frame)
    if not frames:raise RuntimeError('no selected account trades')
    trades=pd.concat(frames,ignore_index=True,sort=False).sort_values('fill_time_ns').reset_index(drop=True)
    manifest=[];links=[]
    for (period,symbol),group in trades.groupby(['period','symbol']):
        start,end=_period_dates(str(period));raw=load_range_flow(str(symbol),start-timedelta(days=1),end+timedelta(days=1),args.cache/str(period))
        for _,row in group.iterrows():
            stamp=_ts(row.fill_time_ns).strftime('%Y%m%d_%H%M');name=f"{period}_{symbol}_{stamp}_{row.narrative_branch}_{row.side}_{row.outcome}.svg"
            render(row,raw,args.output/name);manifest.append({**row.to_dict(),'chart':name});links.append((name,row))
    pd.DataFrame(manifest).to_csv(args.output/'cases_manifest.csv',index=False)
    html=['<!doctype html><meta charset="utf-8"><title>Coherent liquidity selected trades</title>','<style>body{font:14px sans-serif;background:#f5f7f9}a{display:block;margin:8px;padding:10px;background:#fff;border:1px solid #ddd;text-decoration:none;color:#17212c}</style>','<h1>Every selected coherent-liquidity account trade</h1>']
    for name,row in links:html.append(f'<a href="{escape(name)}">{escape(str(row.period))} {escape(str(row.symbol))} {escape(str(_ts(row.fill_time_ns)))} {escape(str(row.narrative_branch))} {escape(str(row.side))} {escape(str(row.outcome))} {_finite(row.net_r):+.2f}R</a>')
    (args.output/'index.html').write_text('\n'.join(html),encoding='utf-8');print({'charts':len(manifest),'output':str(args.output)})


if __name__=='__main__':main()
