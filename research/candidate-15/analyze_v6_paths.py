#!/usr/bin/env python3
"""Post-Nautilus causal/path diagnostics; never changes execution."""
import argparse, json, re
from collections import defaultdict
from io import BytesIO
from math import exp, log
from pathlib import Path
from statistics import median
from zipfile import ZipFile
import pandas as pd

S=("BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT")
C=("open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_volume","taker_buy_quote_volume","ignore")
M=60_000_000_000

def obj(p): return json.loads(p.read_text())
def put(p,x): p.write_text(json.dumps(x,indent=2,sort_keys=True,default=str)+"\n")
def cash(x):
    if x is None or pd.isna(x): return None
    m=re.search(r"[-+]?\d+(?:\.\d+)?",str(x).replace(",","")); return float(m.group()) if m else None

def load(root,s):
    fs=[]
    for p in sorted((root/s).glob("*.zip")):
        with ZipFile(p) as z: b=z.read(z.namelist()[0])
        f=pd.read_csv(BytesIO(b))
        if not set(C).issubset(f.columns): f=pd.read_csv(BytesIO(b),header=None,names=C)
        else: f=f.loc[:,C]
        n=pd.to_numeric(f.open_time,errors="coerce"); f=f.loc[n.notna()].copy(); f.open_time=n[n.notna()].astype("int64"); fs.append(f)
    r=pd.concat(fs).drop_duplicates("open_time").sort_values("open_time"); q=int(r.open_time.iloc[0]); u="ms" if q<10**15 else "us"
    x=pd.DataFrame(index=pd.to_datetime(r.open_time,unit=u,utc=True)+pd.Timedelta(minutes=1))
    for k in ("open","high","low","close","volume","taker_buy_volume"): x[k]=pd.to_numeric(r[k]).to_numpy()
    return x[~x.index.duplicated()].sort_index()

def val(f,t,k="close"):
    q=pd.Timestamp(t,unit="ns",tz="UTC"); return float(f.at[q,k]) if q in f.index else None

def touch(f,a,b,l,d):
    x=f.loc[(f.index>=a)&(f.index<=b)]; h=x.index[x.high>=l] if d=="LONG" else x.index[x.low<=l]
    return int(h[0].value) if len(h) else None

def parents(es):
    out={}; active=None; seen=False
    for e in es:
        k=e.get("type")
        if k=="GLOBAL_ENTRY_SUBMITTED": active=str(e["scenario_id"]); seen=False
        elif k=="ORDER_FILLED" and active and not seen: out[active]=str(e["client_order_id"]); seen=True
        elif k=="GLOBAL_POSITION_CLOSED": active=None; seen=False
    return out

def run(o):
    f={s:load(o/"data",s) for s in S}; plans=obj(o/"submitted_plans.json")["plans"]; life=obj(o/"order_lifecycle.json")["events"]; par=parents(life)
    pos=pd.read_csv(o/"positions.csv"); pm={str(x["opening_order_id"]):x for x in pos.to_dict("records")}
    st=defaultdict(list)
    for line in (o/"scenario_events.raw.jsonl").read_text().splitlines():
        e=json.loads(line)
        if e.get("event_type") in ("QHI_INITIATIVE_ACTIVATED","QHI_INITIATIVE_REFRESHED"): st[str(e["scenario_id"])].append(e)
    for v in st.values(): v.sort(key=lambda e:int(e["observed_time_ns"]))
    rr=[]
    for p in plans:
        d=p["details"]; ro=d["candidate15_v6_route"]; iid=str(ro["initiative_id"]); t=int(p["observed_ts_ns"]); z=[e for e in st[iid] if int(e["observed_time_ns"])<=t][-1]; sd=z["details"]; t2=int(z["observed_time_ns"]); sp=int(sd["confirmation_span_ns"]); t1=t2-sp
        sym=str(p["symbol"]); ac=tuple(ro["accepted_symbols"]); dr=str(p["direction"]); sg=1 if dr=="LONG" else -1
        p1={s:val(f[s],t1) for s in S}; p2={s:val(f[s],t2) for s in S}; pp={s:val(f[s],t) for s in S}
        ap=[sg*log(p2[s]/p1[s]) for s in ac]; aq=[sg*log(pp[s]/p1[s]) for s in ac]; am=median(ap); arm=median(aq); rp=sg*log(p2[sym]/p1[sym]); rq=sg*log(pp[sym]/p1[sym]); parity=p1[sym]*exp(sg*am)
        en=float(p["entry"]); sl=float(p["stop"]); tg=float(p["target"]); risk=abs(en-sl); gain=sg*(parity-en); ahead=gain>0
        unit=abs(float(p["expected_total_loss"]))/max(float(p["quantity"]),1e-12); cr=(gain-en*.0004-parity*.0004)/unit
        parent=par.get(str(p["scenario_id"])); x=pm.get(str(parent)); r={"scenario_id":p["scenario_id"],"symbol":sym,"direction":dr,"accepted_symbols":list(ac),"owner_symbol":sd.get("owner_symbol"),"confirmation_span_minutes":sp/M,"state_to_plan_minutes":(t-t2)/M,"accepted_progress":am,"residual_progress_state":rp,"gap_state":am-rp,"accepted_progress_plan":arm,"residual_progress_plan":rq,"gap_plan":arm-rq,"parity_price":parity,"parity_ahead":ahead,"parity_gross_r":gain/risk,"parity_costed_r":cr,"entry":en,"stop":sl,"external_target":tg,"external_net_r":p["net_r"],"mss_body_atr":d.get("mss_body_atr"),"mss_signed_flow":d.get("mss_signed_flow"),"target_source":d.get("target_pool_source"),"filled":x is not None}
        if x:
            a=pd.Timestamp(x["ts_opened"]); b=pd.Timestamp(x["ts_closed"]); path=f[sym].loc[(f[sym].index>=a)&(f[sym].index<=b)]; av=float(x["avg_px_open"])
            if dr=="LONG": mfe=(path.high.max()-av)/risk; mae=(av-path.low.min())/risk
            else: mfe=(av-path.low.min())/risk; mae=(path.high.max()-av)/risk
            stop=touch(f[sym],a,b,sl,"SHORT" if dr=="LONG" else "LONG"); pt=touch(f[sym],a,b,parity,dr) if ahead else None; pnl=cash(x["realized_pnl"]); pl=cash(p["expected_total_loss"])
            r.update(opened=str(a),closed=str(b),duration_minutes=None if pd.isna(x.get("duration_ns")) else float(x["duration_ns"])/M,realized_pnl=pnl,win=pnl>0,realized_r=pnl/pl,mfe_r=float(mfe),mae_r=float(mae),stop_touch_ts_ns=stop,parity_touch_ts_ns=pt,parity_before_stop=bool(pt is not None and (stop is None or pt<stop)),is_snapshot=bool(x.get("is_snapshot")))
        rr.append(r)
    filled=[r for r in rr if r["filled"]]; valid=[r for r in filled if r["parity_ahead"]]
    out={"schema":"candidate-15-v6-causal-path-diagnostics-v1","diagnostic_only":True,"does_not_modify_execution":True,"submitted_plans":len(rr),"filled_trades":len(filled),"wins":sum(r["win"] for r in filled),"losses":sum(not r["win"] for r in filled),"parity_ahead_at_entry":len(valid),"parity_before_stop":sum(r["parity_before_stop"] for r in valid),"records":rr}
    put(o/"path_diagnostics.json",out); print(json.dumps({k:v for k,v in out.items() if k!="records"},indent=2))

if __name__=="__main__":
    a=argparse.ArgumentParser(); a.add_argument("output_dir",type=Path); run(a.parse_args().output_dir.resolve())
