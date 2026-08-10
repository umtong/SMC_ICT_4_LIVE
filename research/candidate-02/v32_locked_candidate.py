from __future__ import annotations

import math
import numpy as np
import pandas as pd

ENTRY_RATE = 0.0007
STOP_RATE = 0.0008
TARGET_RATE = 0.00025
FUND_RATE = 0.0001
RISK = 0.03


def _utc_ts(value):
    ts=pd.Timestamp(value)
    return ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')


def add_minute_atr(df,n=60):
    x=df.copy();prev=x.close.shift(1)
    x['tr']=np.maximum.reduce([(x.high-x.low).to_numpy(),(x.high-prev).abs().to_numpy(),(x.low-prev).abs().to_numpy()])
    x['atr']=x.tr.shift(1).rolling(n,min_periods=n//2).median()
    return x


def cost_target(entry, stop, side, target_r):
    # per unit planned expected loss includes stop distance plus costs
    per_unit=abs(entry-stop)+entry*(ENTRY_RATE+FUND_RATE)+stop*STOP_RATE
    if side==1:
        # net = target-entry - entry costs - target cost - funding
        target=(entry*(1+ENTRY_RATE+FUND_RATE)+target_r*per_unit)/(1-TARGET_RATE)
    else:
        # net = entry-target - entry costs - target cost - funding
        target=(entry*(1-ENTRY_RATE-FUND_RATE)-target_r*per_unit)/(1+TARGET_RATE)
    return target, per_unit


def generate_balanced_auction_reclaim_signals(
    m1,b5,start,end,
    formation_bars=12,max_range_atr=4.0,
    eff_min=.15,eff_max=.25,touch_tol_atr=.25,min_touches=2,
    wait_bars=12,min_sweep_atr=.10,classify_bars=6,
    reclaim_depth_atr=.40,accept_closes=2,
    stop_buffer_atr=.15,target_r=3.0,max_hold=720,cooldown_bars=6,
):
    start=_utc_ts(start); end=_utc_ts(end)
    x=b5.copy()
    sigs=[]
    state=None
    cooldown_until=-1
    i=formation_bars
    while i<len(x):
        ts=x.index[i]
        if ts>=end: break
        if state is None:
            if i<=cooldown_until or ts<start-pd.Timedelta(hours=2):
                i+=1; continue
            hist=x.iloc[i-formation_bars:i]
            atr=float(np.nanmedian(hist["tr"])) if "tr" in hist else float(x.iloc[i-1].atr)
            if not np.isfinite(atr) or atr<=0:
                i+=1;continue
            hi=float(hist.high.max());lo=float(hist.low.min());span=hi-lo
            path=float(hist.close.diff().abs().sum())
            eff=abs(float(hist.close.iloc[-1]-hist.open.iloc[0]))/(path+1e-12)
            if span/atr>max_range_atr or not(eff>eff_min and eff<=eff_max):
                i+=1;continue
            up_touches=int((hist.high>=hi-touch_tol_atr*atr).sum())
            dn_touches=int((hist.low<=lo+touch_tol_atr*atr).sum())
            if up_touches<min_touches or dn_touches<min_touches:
                i+=1;continue
            state={"formed_i":i,"hi":hi,"lo":lo,"atr":atr,"wait_until":i+wait_bars,
                   "sweep_side":None,"extreme":None,"class_start":None,"outside":0,
                   "eff":eff,"span_atr":span/atr}
        row=x.iloc[i]
        if state["sweep_side"] is None:
            if i>state["wait_until"]:
                state=None;cooldown_until=i+cooldown_bars;i+=1;continue
            up=(float(row.high)-state["hi"])/state["atr"]
            dn=(state["lo"]-float(row.low))/state["atr"]
            if up>=min_sweep_atr or dn>=min_sweep_atr:
                # if both, choose larger normalized excursion, deterministic.
                side=1 if up>=dn else -1
                state["sweep_side"]=side
                state["class_start"]=i
                state["extreme"]=float(row.high if side==1 else row.low)
                outside=(float(row.close)>state["hi"]) if side==1 else (float(row.close)<state["lo"])
                state["outside"]=1 if outside else 0
        else:
            side=state["sweep_side"]
            state["extreme"]=(max(state["extreme"],float(row.high)) if side==1
                              else min(state["extreme"],float(row.low)))
            outside=(float(row.close)>state["hi"]) if side==1 else (float(row.close)<state["lo"])
            state["outside"]=state["outside"]+1 if outside else 0
            if state["outside"]>=accept_closes:
                state=None;cooldown_until=i+cooldown_bars;i+=1;continue
            if i-state["class_start"]>classify_bars:
                state=None;cooldown_until=i+cooldown_bars;i+=1;continue
            reclaim=((state["hi"]-float(row.close))/state["atr"]>=reclaim_depth_atr
                     if side==1 else
                     (float(row.close)-state["lo"])/state["atr"]>=reclaim_depth_atr)
            if reclaim:
                trade_side=-side
                stop=(state["extreme"]+stop_buffer_atr*state["atr"] if trade_side==-1
                      else state["extreme"]-stop_buffer_atr*state["atr"])
                sigs.append(dict(
                    entry_ts=ts,side=trade_side,stop=stop,target_r=target_r,max_hold=max_hold,
                    state="BALANCED_AUCTION_RECLAIM",priority=3,
                    score=state["span_atr"]+abs(float(row.close-(state["hi"] if side==1 else state["lo"])))/state["atr"],
                    event_ts=ts,
                    details={"formation_eff":state["eff"],"span_atr":state["span_atr"],
                             "sweep_side":side,"hi":state["hi"],"lo":state["lo"]}
                ))
                state=None;cooldown_until=i+cooldown_bars
        i+=1
    return pd.DataFrame(sigs)


def filter_signals_htf(signals,b5,threshold=0.0,hours=4):
    if signals is None or len(signals)==0:return pd.DataFrame()
    x=b5.copy()
    bars=hours*12
    trend=(x.close.shift(1)-x.close.shift(1+bars))/x.atr
    keep=[]
    for _,r in signals.iterrows():
        ts=pd.Timestamp(r.event_ts)
        if ts not in trend.index:continue
        v=trend.loc[ts]
        if isinstance(v,pd.Series):v=v.iloc[-1]
        if np.isfinite(v) and int(r.side)*float(v)>=threshold:
            keep.append(r.to_dict())
    return pd.DataFrame(keep)


def generate_funding_signals(m1,met,prem,fund,start,end,
                             pre_minutes=60,range_minutes=30,min_move_atr=.5,
                             sweep_atr=.05,reclaim_atr=.02,window_minutes=15,
                             stop_buffer_atr=.10,target_r=1.25,max_hold=60):
    start=_utc_ts(start);end=_utc_ts(end);df=add_minute_atr(m1,60)
    sigs=[]
    for ts,fr in fund.loc[start-pd.Timedelta(minutes=1):end].iterrows():
        ts=ts.floor('min')
        if not(start<=ts<end):continue
        pre=df.loc[ts-pd.Timedelta(minutes=pre_minutes):ts-pd.Timedelta(minutes=1)]
        rng=df.loc[ts-pd.Timedelta(minutes=range_minutes):ts-pd.Timedelta(minutes=1)]
        mm=met.loc[ts-pd.Timedelta(minutes=pre_minutes):ts]
        oi=mm.sum_open_interest.where(mm.sum_open_interest>0).dropna()
        if len(pre)<pre_minutes*.8 or len(rng)<range_minutes*.8 or len(oi)<2:continue
        atr=float(pre.atr.iloc[-1]);move=float(pre.close.iloc[-1]-pre.open.iloc[0]);d=int(np.sign(move))
        if d==0 or not np.isfinite(atr) or abs(move)/atr<min_move_atr:continue
        oi_ret=float(oi.iloc[-1]/oi.iloc[0]-1)
        if oi_ret<=0:continue
        # funding-paying inventory must align with pre-settlement move
        rate=float(fr.last_funding_rate)
        if rate==0 or int(np.sign(rate))!=d:continue
        boundary=float(rng.high.max() if d==1 else rng.low.min())
        win=df.loc[ts:ts+pd.Timedelta(minutes=window_minutes-1)]
        ext=None
        for cts,row in win.iterrows():
            if d==1:
                if float(row.high)>=boundary+sweep_atr*atr:ext=max(ext or -np.inf,float(row.high))
                confirm=ext is not None and float(row.close)<boundary-reclaim_atr*atr and float(row.close)<float(row.open)
                if confirm:side=-1;stop=ext+stop_buffer_atr*atr
            else:
                if float(row.low)<=boundary-sweep_atr*atr:ext=min(ext or np.inf,float(row.low))
                confirm=ext is not None and float(row.close)>boundary+reclaim_atr*atr and float(row.close)>float(row.open)
                if confirm:side=1;stop=ext-stop_buffer_atr*atr
            if confirm:
                sigs.append(dict(entry_ts=cts+pd.Timedelta(minutes=1),side=side,stop=stop,target_r=target_r,max_hold=max_hold,
                                 state='FUNDING_RESET',priority=3,score=abs(move)/atr+1000*abs(rate),
                                 event_ts=ts,details={'move_atr':abs(move)/atr,'oi_ret':oi_ret,'funding_rate':rate}))
                break
    return pd.DataFrame(sigs)


def backtest_signal_portfolio(m1,start,end,signal_frames,starting_nav=100000):
    start=_utc_ts(start);end=_utc_ts(end);df=add_minute_atr(m1,60)
    sig=pd.concat([s for s in signal_frames if s is not None and len(s)],ignore_index=True) if any(s is not None and len(s) for s in signal_frames) else pd.DataFrame()
    if len(sig):
        sig['entry_ts']=pd.to_datetime(sig.entry_ts,utc=True)
        # at same timestamp choose priority then score
        sig=sig.sort_values(['entry_ts','priority','score'],ascending=[True,False,False])
        grouped={ts:g for ts,g in sig.groupby('entry_ts')}
    else: grouped={}
    minute=df.loc[start:end-pd.Timedelta(minutes=1)]
    nav=starting_nav;peak=nav;maxdd=0;pos=None;trades=[];skipped=[];cool=start
    for ts,row in minute.iterrows():
        # manage existing first; cannot enter/exit same open except if previous position closes intrabar
        if pos is not None:
            side=pos['side'];o,h,l,c=map(float,[row.open,row.high,row.low,row.close]);qty=pos['qty'];reason=None
            sh=l<=pos['stop'] if side==1 else h>=pos['stop'];th=h>=pos['target'] if side==1 else l<=pos['target']
            if sh:px=min(pos['stop'],o) if side==1 else max(pos['stop'],o);rate=STOP_RATE;reason='STOP'
            elif th:px=pos['target'];rate=TARGET_RATE;reason='TARGET'
            elif (ts-pos['entry_ts']).total_seconds()/60>=pos['max_hold']:
                px=c;rate=ENTRY_RATE;reason='TIME'
            mtm=pos['nav_before']+qty*side*(c-pos['entry'])-pos['entry_cost']-qty*c*ENTRY_RATE
            peak=max(peak,mtm);maxdd=min(maxdd,mtm/peak-1)
            if reason:
                nav=pos['nav_before']+qty*side*(px-pos['entry'])-pos['entry_cost']-qty*px*rate
                pnl=nav-pos['nav_before'];r=pnl/pos['risk_budget']
                trades.append({**pos,'exit_ts':ts,'exit_px':px,'reason':reason,'pnl':pnl,'r':r,'nav_after':nav})
                peak=max(peak,nav);maxdd=min(maxdd,nav/peak-1);pos=None;cool=ts+pd.Timedelta(minutes=1)
            # even if closes this minute, don't enter another at same minute open (signal entry is open)
            if ts in grouped:
                skipped.extend(grouped[ts].assign(skip='BUSY').to_dict('records'))
            continue
        if ts in grouped:
            g=grouped[ts]
            if ts<cool:
                skipped.extend(g.assign(skip='COOLDOWN').to_dict('records'));continue
            s=g.iloc[0]
            if len(g)>1:skipped.extend(g.iloc[1:].assign(skip='LOWER_PRIORITY').to_dict('records'))
            entry=float(row.open);side=int(s.side);stop=float(s.stop)
            if not((side==1 and stop<entry)or(side==-1 and stop>entry)):
                skipped.append({**s.to_dict(),'skip':'INVALID_GEOMETRY'});continue
            target,pu=cost_target(entry,stop,side,float(s.target_r))
            qty=nav*RISK/pu;ec=qty*entry*(ENTRY_RATE+FUND_RATE)
            pos={**s.to_dict(),'entry':entry,'target':target,'qty':qty,'per_unit':pu,'nav_before':nav,
                 'risk_budget':nav*RISK,'entry_cost':ec,'entry_ts':ts}
            nav-=ec
    # close at end
    if pos is not None:
        ts=minute.index[-1];c=float(minute.iloc[-1].close);qty=pos['qty'];side=pos['side']
        nav=pos['nav_before']+qty*side*(c-pos['entry'])-pos['entry_cost']-qty*c*ENTRY_RATE
        pnl=nav-pos['nav_before'];r=pnl/pos['risk_budget'];trades.append({**pos,'exit_ts':ts,'exit_px':c,'reason':'END','pnl':pnl,'r':r,'nav_after':nav})
    days=(end-start).total_seconds()/86400;tr=pd.DataFrame(trades);gp=tr.loc[tr.pnl>0,'pnl'].sum() if len(tr) else 0;gl=tr.loc[tr.pnl<0,'pnl'].sum() if len(tr) else 0
    return {'metrics':dict(raw_signals=len(sig),trades=len(tr),trades_per_day=len(tr)/days,win_rate=(tr.pnl>0).mean() if len(tr) else 0,
                           geom=(nav/starting_nav)**(1/days)-1 if nav>0 else -1,pf=gp/abs(gl) if gl<0 else np.inf,
                           avg_r=tr.r.mean() if len(tr) else 0,maxdd=maxdd,final_nav=nav,
                           state_counts=tr.state.value_counts().to_dict() if len(tr) else {},
                           state_metrics=tr.groupby('state').agg(n=('pnl','size'),win=('pnl',lambda x:(x>0).mean()),avg_r=('r','mean')).to_dict('index') if len(tr) else {}),
            'trades':tr,'signals':sig,'skipped':pd.DataFrame(skipped)}


def generate_v32_signals(m1, b5, metrics, funding, start, end):
    auction = generate_balanced_auction_reclaim_signals(
        m1, b5, start, end,
        formation_bars=12,
        max_range_atr=4.0,
        eff_min=0.15,
        eff_max=0.25,
        touch_tol_atr=0.25,
        min_touches=2,
        wait_bars=12,
        min_sweep_atr=0.10,
        classify_bars=6,
        reclaim_depth_atr=0.40,
        accept_closes=2,
        stop_buffer_atr=0.15,
        target_r=3.0,
        max_hold=720,
        cooldown_bars=6,
    )
    auction = filter_signals_htf(auction, b5, threshold=0.0, hours=4)
    funding_reset = generate_funding_signals(
        m1, metrics, None, funding, start, end,
        pre_minutes=60,
        range_minutes=30,
        min_move_atr=0.50,
        sweep_atr=0.05,
        reclaim_atr=0.02,
        window_minutes=15,
        stop_buffer_atr=0.10,
        target_r=1.25,
        max_hold=60,
    )
    if len(funding_reset):
        funding_reset = funding_reset.copy()
        funding_reset["priority"] = 5
    return funding_reset, auction


def run_v32_week(m1, b5, metrics, funding, start, end, starting_nav=100000.0):
    funding_reset, auction = generate_v32_signals(
        m1, b5, metrics, funding, start, end,
    )
    return backtest_signal_portfolio(
        m1,
        start,
        end,
        [funding_reset, auction],
        starting_nav=starting_nav,
    )
