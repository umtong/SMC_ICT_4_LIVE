"""Count causal v67 event-funnel stages without simulating fills or PnL."""
from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np
import pandas as pd
from v53_nt_backtest import _resolve_input_paths
from v53_nt_core import CostConfig,load_feature_matrix,load_raw_one_minute
from v61_family_core import cost_after_reward_risk
from v67_two_close_acceptance_core import TwoCloseConfig,build_state
import v66_depth_flow_core as parent

def main()->None:
    ap=argparse.ArgumentParser();ap.add_argument("--config",type=Path,required=True);ap.add_argument("--input-root",type=Path,required=True);ap.add_argument("--output",type=Path,required=True);args=ap.parse_args()
    cfg=json.loads(args.config.read_text());config=TwoCloseConfig.from_mapping(cfg["scenario"]);costs=CostConfig.from_mapping(cfg["costs"])
    npz,cols,rawdir=_resolve_input_paths(args.input_root);features=load_feature_matrix(npz,cols);raw=load_raw_one_minute(rawdir);state=build_state(features,config)
    start=pd.Timestamp(cfg["validation"]["first_week_start"],tz="UTC");end=start+pd.Timedelta(days=7)
    x=state.join(raw[["high","low"]].rename(columns={"high":"high_1m","low":"low_1m"}),how="inner");atr_series=parent._atr(raw,config.atr_lookback_minutes)
    keys=["rows","finite","flow","volume","withdrawal","opposite_stable","efficient","boundary_swept","closed_outside","two_close_acceptance","valid_geometry","rr_band"]
    count={k:0 for k in keys};examples=[]
    for ts,row in x.loc[(x.index>=start)&(x.index<end)].iterrows():
        count["rows"]+=1
        names=("flow_ratio_window","flow_abs_threshold","quote_volume_window","volume_threshold","impact_efficiency","impact_efficiency_high_threshold","depth_change_threshold")
        if any(not math.isfinite(float(row[n])) for n in names):continue
        count["finite"]+=1;direction=int(np.sign(float(row["flow_ratio_window"])))
        if direction==0 or abs(float(row["flow_ratio_window"]))<float(row["flow_abs_threshold"]):continue
        count["flow"]+=1
        if float(row["quote_volume_window"])<float(row["volume_threshold"]):continue
        count["volume"]+=1
        same=float(row["ask_depth_change_window"] if direction>0 else row["bid_depth_change_window"])
        opposite=float(row["bid_depth_change_window"] if direction>0 else row["ask_depth_change_window"])
        if same>-float(row["depth_change_threshold"]):continue
        count["withdrawal"]+=1
        if opposite<-0.05:continue
        count["opposite_stable"]+=1
        if float(row["impact_efficiency"])<float(row["impact_efficiency_high_threshold"]):continue
        count["efficient"]+=1
        prior=raw.loc[(raw.index>=ts-pd.Timedelta(minutes=config.boundary_minutes))&(raw.index<ts)];window=raw.loc[(raw.index>ts-pd.Timedelta(minutes=config.impact_window_minutes))&(raw.index<=ts)]
        if len(prior)<config.boundary_minutes-1 or window.empty:continue
        high,low=float(prior["high"].max()),float(prior["low"].min());width=high-low;atr=float(atr_series.asof(ts))
        if not math.isfinite(width) or width<=0 or not math.isfinite(atr) or atr<=0:continue
        boundary=high if direction>0 else low;extreme=float(window["high"].max() if direction>0 else window["low"].min())
        swept=extreme>=high+config.boundary_break_atr*atr if direction>0 else extreme<=low-config.boundary_break_atr*atr
        if not swept:continue
        count["boundary_swept"]+=1
        outside=float(row["close"])>high if direction>0 else float(row["close"])<low
        if not outside:continue
        count["closed_outside"]+=1
        future=x.loc[(x.index>ts)&(x.index<=min(end,ts+pd.Timedelta(minutes=config.confirmation_window_minutes)))];run=0;confirmation=None
        for observed,r in future.iterrows():
            out=float(r["close"])>boundary if direction>0 else float(r["close"])<boundary;run=run+1 if out else 0
            if run>=2:confirmation=(pd.Timestamp(observed),float(r["close"]));break
        if confirmation is None:continue
        count["two_close_acceptance"]+=1;observed,entry=confirmation
        if direction>0:side,stop,target="BUY",high-config.stop_buffer_atr*atr,high+config.target_range_extension*width
        else:side,stop,target="SELL",low+config.stop_buffer_atr*atr,low-config.target_range_extension*width
        geometry=stop<entry<target if side=="BUY" else target<entry<stop
        if not geometry:continue
        count["valid_geometry"]+=1
        rr=cost_after_reward_risk(entry=entry,stop=stop,target=target,side=side,costs=costs)
        if not math.isfinite(rr) or not config.minimum_cost_after_rr<=rr<=config.maximum_cost_after_rr:continue
        count["rr_band"]+=1;examples.append({"setup":ts.isoformat(),"observed":observed.isoformat(),"side":side,"entry":entry,"stop":stop,"target":target,"rr":rr})
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps({"counts":count,"examples":examples},indent=2));print(json.dumps({"counts":count,"examples":examples},indent=2))
if __name__=="__main__":main()
