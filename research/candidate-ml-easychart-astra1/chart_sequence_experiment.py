"""Sequence-shaped auction direction using the existing MiniRocket algorithm.

A chart is an ordered trajectory, not a bag of indicator totals. This experiment
keeps the v13 structural paths, fixed stops/targets and Nautilus account, but
replaces the manually summarized state with closed 5m/15m/60m OHLC/volume/flow
trajectories. Both trade directions use exactly the same normalized channels.

The transform is aeon MiniRocket 1.5.0 (Dempster/Schmidt/Webb, arXiv:2012.08791).
Its biases see development observations only. An L2-regularized binomial model
learns the correction to the no-drift barrier odds; later chronological data
calibrate that correction. Neither a win-rate target nor trade quota is fitted.
"""
from pathlib import Path
from dataclasses import dataclass
import gc,hashlib,json,pickle,subprocess,sys,time
import numpy as np
import pandas as pd
from scipy.special import expit
from scipy.optimize import minimize
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score
from astra_policy import MINUTE
from directional_transition_model import path_weights

SCALES=(5,15,60)
LENGTH=64
KERNELS=2016
STORES={}

class ChartStore:
    def __init__(self,raws):
        self.frames={}
        for symbol,raw in raws.items():
            d=raw[['ts','open','high','low','close','volume','taker_buy_volume','quote_volume']].copy()
            d['delta']=2*d.taker_buy_volume-d.volume
            for tf in SCALES:
                d['bucket']=((d.ts-1)//(tf*MINUTE)+1)*(tf*MINUTE)
                g=d.groupby('bucket').agg(open=('open','first'),high=('high','max'),low=('low','min'),
                    close=('close','last'),volume=('volume','sum'),delta=('delta','sum'),count=('ts','size'))
                g=g[g['count']==tf]
                self.frames[symbol,tf]=(g.index.to_numpy(dtype=np.int64),g[['open','high','low','close','volume','delta']].to_numpy(dtype=np.float32))

    def sequences(self,rows):
        n=len(rows);result=np.empty((n,8*len(SCALES)+1,LENGTH),dtype=np.float32)
        for symbol in rows.symbol.unique():
            positions=np.flatnonzero(rows.symbol.to_numpy()==symbol)
            r=rows.iloc[positions]
            times=r.observed_time_ns.to_numpy(dtype=np.int64)
            side=r.side.to_numpy(dtype=np.float32)
            entry=r.entry.to_numpy(dtype=np.float32)
            for k,tf in enumerate(SCALES):
                stamps,data=self.frames[symbol,tf]
                right=np.searchsorted(stamps,times,side='right')-1
                if np.any(right<LENGTH-1):raise ValueError('chart observation lacks completed higher-timeframe history')
                indexes=right[:,None]-np.arange(LENGTH-1,-1,-1)[None,:]
                a=data[indexes]
                # Every selected aggregate was closed no later than the decision.
                if np.any(stamps[right]>times):raise AssertionError('future chart aggregate')
                unit=np.maximum(np.mean(a[:,:,1]-a[:,:,2],axis=1),entry*1e-8)
                o=side[:,None]*(a[:,:,0]-entry[:,None])/unit[:,None]
                h=np.where(side[:,None]>0,a[:,:,1],a[:,:,2])
                l=np.where(side[:,None]>0,a[:,:,2],a[:,:,1])
                h=side[:,None]*(h-entry[:,None])/unit[:,None]
                l=side[:,None]*(l-entry[:,None])/unit[:,None]
                c=side[:,None]*(a[:,:,3]-entry[:,None])/unit[:,None]
                vbase=np.maximum(np.median(a[:,:,4],axis=1),1e-8)
                volume=np.log1p(a[:,:,4]/vbase[:,None])
                flow=side[:,None]*a[:,:,5]/np.maximum(a[:,:,4],1e-8)
                stop=side*(r.stop.to_numpy(dtype=np.float32)-entry)/unit
                target=side*(r.target.to_numpy(dtype=np.float32)-entry)/unit
                result[positions,8*k:8*k+8,:]=np.stack((o,h,l,c,volume,flow,
                    np.repeat(stop[:,None],LENGTH,axis=1),np.repeat(target[:,None],LENGTH,axis=1)),axis=1)
        result[:,-1,:]=np.linspace(-1.,0.,LENGTH,dtype=np.float32)
        if not np.isfinite(result).all():raise ValueError('non-finite observed chart sequence')
        return result


def month_of(ts):return pd.Timestamp(int(ts),tz='UTC').strftime('%Y-%m')

def sequences(rows):
    rows=rows.reset_index(drop=True)
    months=pd.to_datetime(rows.observed_time_ns,utc=True).dt.strftime('%Y-%m').to_numpy()
    result=np.empty((len(rows),8*len(SCALES)+1,LENGTH),dtype=np.float32)
    for month in np.unique(months):
        ix=np.flatnonzero(months==month)
        if month not in STORES:raise ValueError('observed chart store was not supplied: '+month)
        result[ix]=STORES[month].sequences(rows.iloc[ix].reset_index(drop=True))
    return result


def transform_rows(transformer,rows,batch=2048):
    chunks=[]
    for left in range(0,len(rows),batch):
        x=sequences(rows.iloc[left:left+batch])
        chunks.append(np.asarray(transformer.transform(x),dtype=np.float32))
    return np.concatenate(chunks,axis=0) if chunks else np.empty((0,KERNELS),dtype=np.float32)

class ChartDecision:
    def __init__(self,transformer,scaler,coef,intercept,a,b,trained_through):
        self.transformer=transformer;self.scaler=scaler;self.coef=np.asarray(coef,dtype=np.float32)
        self.intercept=float(intercept);self.a=float(a);self.b=float(b);self.trained_through=int(trained_through)
    def probabilities(self,plans):
        result={}
        for left in range(0,len(plans),2048):
            batch=plans[left:left+2048]
            rows=pd.DataFrame([{'symbol':p.symbol,'side':p.side.value,'observed_time_ns':p.observed_time_ns,
                'entry':p.entry,'stop':p.stop,'target':p.target,'gross_rr':p.gross_rr} for p in batch])
            x=np.asarray(self.transformer.transform(sequences(rows)),dtype=np.float32)
            x=self.scaler.transform(x,copy=False)
            delta=x@self.coef+self.intercept
            probability=expit(-np.log(rows.gross_rr.to_numpy(dtype=float))+self.a*delta+self.b)
            result.update((p.plan_id,float(q)) for p,q in zip(batch,probability))
        return result


def inspect_prior_execution(base,labels):
    # Directly distinguish a simulator/label mismatch from wrong decisions.
    # This is not another validation framework or a promotion score.
    output=[]
    cols=['plan_id','label_target','label_closed','label_net_r']
    for version in (13,14):
        root=Path('research_results')/f'astra1_control_v{version}'
        for path in sorted(root.glob('*/trades.csv')):
            trades=pd.read_csv(path)
            if not len(trades):continue
            if 'evaluation_censored' in trades:trades=trades[~trades.evaluation_censored]
            joined=trades.merge(labels[cols],on='plan_id',how='left',validate='one_to_one')
            matched=joined[joined.label_target.notna()].copy()
            if not len(matched):continue
            disagreement=(matched.pnl>0)!=(matched.label_target>0)
            output.append({'path':str(path),'matched':len(matched),'actual_wins':int((matched.pnl>0).sum()),
                'counterfactual_targets':int(matched.label_target.sum()),'outcome_disagreements':int(disagreement.sum()),
                'disagreements':matched.loc[disagreement,['plan_id','entry','stop','target','entry_fill','exit_fill','pnl','label_net_r','label_closed']].head(10).to_dict('records')})
    base.write(base.OUT/'prior_decision_execution_comparison.json',output)


def fit(base,labels,train_end,cal_end):
    from aeon.transformations.collection.convolution_based import MiniRocket
    t0=time.time()
    # A 64-hour chart requires actual prehistory. Skip only the unavailable
    # beginning of a file, not an unfavorable outcome or a market state.
    first={month:min(int(stamps[0]) for stamps,_ in store.frames.values()) for month,store in STORES.items()}
    months=pd.to_datetime(labels.observed_time_ns,utc=True).dt.strftime('%Y-%m')
    available=np.array([t>=first[m]+64*60*MINUTE for t,m in zip(labels.observed_time_ns,months)])
    labels=labels.loc[available].copy()
    train=labels[labels.label_closed<base.ns(train_end)].copy()
    cal=labels[(labels.observed_time_ns>=base.ns(train_end))&(labels.label_closed<base.ns(cal_end))].copy()
    if len(train)<1000 or len(cal)<200:raise ValueError('insufficient completed sequence observations')
    inspect_prior_execution(base,labels)
    proto=train.drop_duplicates('causal_event_id',keep='first')
    proto=proto.iloc[np.linspace(0,len(proto)-1,min(4096,len(proto)),dtype=int)]
    transformer=MiniRocket(n_kernels=KERNELS,n_jobs=2,random_state=20260905)
    transformer.fit(sequences(proto))
    x=transform_rows(transformer,train)
    weight=path_weights(train);weight/=weight.sum()
    scaler=StandardScaler(copy=False).fit(x,sample_weight=weight)
    x=scaler.transform(x,copy=False)
    offset=-np.log(train.gross_rr.to_numpy(dtype=float));y=train.label_target.to_numpy(dtype=float)
    dim=x.shape[1];regularization=.01
    def objective(theta):
        beta=np.asarray(theta[:-1],dtype=np.float32)
        z=offset+x@beta+theta[-1]
        probability=expit(z)
        loss=float(np.sum(weight*(np.logaddexp(0,z)-y*z))+.5*regularization*np.dot(beta,beta))
        residual=(weight*(probability-y)).astype(np.float32)
        gradient=np.r_[np.asarray(x.T@residual,dtype=float)+regularization*beta,float(residual.sum())]
        return loss,gradient
    solution=minimize(objective,np.zeros(dim+1),jac=True,method='L-BFGS-B',
        options={'maxiter':180,'gtol':1e-4,'ftol':1e-9,'maxls':30})
    if not solution.success:raise RuntimeError('sequence probability fit did not converge: '+solution.message)
    coef=solution.x[:-1];intercept=solution.x[-1]
    del x;gc.collect()
    cx=scaler.transform(transform_rows(transformer,cal),copy=False)
    delta=cx@np.asarray(coef,dtype=np.float32)+intercept
    co=-np.log(cal.gross_rr.to_numpy(dtype=float));cy=cal.label_target.to_numpy(dtype=float)
    cw=path_weights(cal);cw/=cw.sum()
    def calibration(ab):
        a,b=ab;z=co+a*delta+b;p=expit(z)
        loss=np.sum(cw*(np.logaddexp(0,z)-cy*z))+.002*(a-1)**2+.002*b*b
        grad=[np.sum(cw*(p-cy)*delta)+.004*(a-1),np.sum(cw*(p-cy))+.004*b]
        return float(loss),np.array(grad)
    fitted=minimize(calibration,[1.,0.],jac=True,method='L-BFGS-B',bounds=((0.,None),(None,None)))
    if not fitted.success:raise RuntimeError('sequence calibration did not converge')
    a,b=fitted.x;prediction=expit(co+a*delta+b)
    info={'algorithm':'aeon-1.5.0 MiniRocket + regularized conditional barrier logits',
        'train_observations':len(train),'train_paths':int(train.causal_event_id.nunique()),
        'calibration_observations':len(cal),'calibration_paths':int(cal.causal_event_id.nunique()),
        'scales':SCALES,'closed_bars_per_scale':LENGTH,'kernels':dim,'regularization':regularization,
        'calibration_slope':float(a),'calibration_intercept':float(b),
        'calibration_auc':float(roc_auc_score(cy,prediction)),
        'geometry_baseline_auc':float(roc_auc_score(cy,expit(co))),
        'calibration_brier':float(np.mean((prediction-cy)**2)),
        'train_end':train_end,'calibration_end':cal_end,'elapsed_seconds':time.time()-t0,
        'model_input_excludes':'symbol identity, absolute price, calendar identity, partial higher bars, future outcomes'}
    decision=ChartDecision(transformer,scaler,coef,intercept,a,b,base.ns(cal_end))
    base.write(base.OUT/'model.json',info)
    (base.OUT/'decision.pkl').write_bytes(pickle.dumps(decision))
    print('CHART_SEQUENCE_MODEL',json.dumps(info),flush=True)
    return decision


def execute(base,request):
    subprocess.run([sys.executable,'-m','pip','install','aeon==1.5.0'],check=True)
    original=base.Tape
    class ChartTape(original):
        def __init__(self,month):
            super().__init__(month);STORES[month]=ChartStore(self.raw)
        def plans(self):
            plans,stats,no_trades=super().plans()
            first=int(next(iter(self.raw.values())).ts.iloc[0])
            return [p for p in plans if p.observed_time_ns>=first+65*60*MINUTE],stats,no_trades
    base.Tape=ChartTape
    base.fit=lambda labels,train_end,cal_end:fit(base,labels,train_end,cal_end)
    base.main()
