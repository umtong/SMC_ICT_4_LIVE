#!/usr/bin/env python3
"""Diagnostic-only causal state funnel for candidate-02 v95.

This script never simulates orders, fills, PnL or NAV. It replays the locked
v95 detector/state-machine over the same completed data and counts each causal
transition. The final emitted signal count is asserted equal to the production
core output, so the diagnostic cannot silently redefine the strategy.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, load_feature_matrix, load_raw_one_minute
import v95_mature_swing_breakout_core as core

UTC = timezone.utc


def _resolve(input_root: Path) -> tuple[Path, Path, Path]:
    candidates = [input_root, *[p for p in input_root.rglob("candidate-02-v48-first-week") if p.is_dir()]]
    feature_root = next((p for p in candidates if (p / "v48_features.npz").is_file() and (p / "columns.json").is_file()), None)
    raw_root = next((p for p in input_root.rglob("binance_1m") if p.is_dir() and any(p.glob("BTCUSDT-1m-*.zip"))), None)
    if feature_root is None or raw_root is None:
        raise FileNotFoundError(f"v95 diagnostic inputs unavailable: feature={feature_root}, raw={raw_root}")
    return feature_root / "v48_features.npz", feature_root / "columns.json", raw_root


def _finite(row: pd.Series, names: Sequence[str]) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


def _qualification_audit(raw: pd.DataFrame, candidates: Sequence[core.SwingLevelCandidate], atr: pd.Series, cfg: core.MatureSwingBreakoutConfig) -> tuple[list[core.MatureSwingLevel], dict[str, int], list[dict[str, Any]]]:
    frame = core._normalise_index(raw[["open", "high", "low", "close"]])
    frame["atr"] = atr.reindex(frame.index)
    index_ns = frame.index.asi8
    counts = {
        "candidates": len(candidates), "invalid_age_contract": 0,
        "breached_during_maturation": 0, "survived_to_maturity": 0,
        "defense_approach_seen": 0, "breached_after_maturity_before_defense": 0,
        "defense_rejection_confirmed": 0, "defense_window_no_rejection": 0,
        "expired_without_defense": 0, "qualified": 0,
    }
    records: list[dict[str, Any]] = []
    output: list[core.MatureSwingLevel] = []
    for level in candidates:
        rec: dict[str, Any] = {"level_id": level.level_id, "side": level.side, "price": level.price}
        earliest_ns = level.confirmation_ns + cfg.minimum_level_age_minutes * core.NS_MINUTE
        if earliest_ns >= level.expiry_ns:
            counts["invalid_age_contract"] += 1; rec["terminal"] = "INVALID_AGE_CONTRACT"; records.append(rec); continue
        left = int(np.searchsorted(index_ns, level.confirmation_ns, side="right"))
        right = int(np.searchsorted(index_ns, level.expiry_ns, side="right"))
        earliest = int(np.searchsorted(index_ns, earliest_ns, side="right"))
        dead = False
        for pos in range(left, min(earliest, right)):
            row = frame.iloc[pos]; av = float(row["atr"])
            if math.isfinite(av) and av > 0 and core._breached(level, row, av, cfg.minimum_level_breach_atr):
                dead = True; rec["maturation_breach_utc"] = pd.Timestamp(frame.index[pos]).isoformat(); break
        if dead:
            counts["breached_during_maturation"] += 1; rec["terminal"] = "BREACHED_DURING_MATURATION"; records.append(rec); continue
        counts["survived_to_maturity"] += 1
        if not cfg.require_defense_memory:
            mature = core.MatureSwingLevel(level.level_id, level.side, level.price, level.pivot_close_ns, level.confirmation_ns, earliest_ns, level.expiry_ns, None, None)
            output.append(mature); counts["qualified"] += 1; rec["terminal"] = "QUALIFIED_WITHOUT_DEFENSE"; records.append(rec); continue
        qualified = None; position = earliest; approaches = 0; windows_no_rejection = 0; breached_after = False
        while position < right:
            row = frame.iloc[position]; av = float(row["atr"])
            if not math.isfinite(av) or av <= 0: position += 1; continue
            if core._breached(level, row, av, cfg.minimum_level_breach_atr):
                breached_after = True; rec["post_maturity_breach_utc"] = pd.Timestamp(frame.index[position]).isoformat(); break
            approached = (float(row["high"]) >= level.price - cfg.defense_approach_atr * av and float(row["close"]) <= level.price) if level.side == "HIGH" else (float(row["low"]) <= level.price + cfg.defense_approach_atr * av and float(row["close"]) >= level.price)
            if not approached: position += 1; continue
            approaches += 1
            confirm_end = min(position + cfg.defense_confirmation_minutes - 1, right - 1)
            invalidated = False
            for cp in range(position, confirm_end + 1):
                cr = frame.iloc[cp]; ca = float(cr["atr"])
                if not math.isfinite(ca) or ca <= 0: continue
                if core._breached(level, cr, ca, cfg.minimum_level_breach_atr): invalidated = True; breached_after = True; break
                rejected = float(cr["close"]) <= level.price - cfg.defense_rejection_atr * ca if level.side == "HIGH" else float(cr["close"]) >= level.price + cfg.defense_rejection_atr * ca
                if rejected:
                    qualified = core.MatureSwingLevel(level.level_id, level.side, level.price, level.pivot_close_ns, level.confirmation_ns, int(index_ns[cp]), level.expiry_ns, int(index_ns[position]), int(index_ns[cp])); break
            if qualified is not None or invalidated: break
            windows_no_rejection += 1; position = confirm_end + 1
        if approaches: counts["defense_approach_seen"] += 1
        if windows_no_rejection: counts["defense_window_no_rejection"] += 1
        if qualified is not None:
            output.append(qualified); counts["defense_rejection_confirmed"] += 1; counts["qualified"] += 1; rec["terminal"] = "QUALIFIED_DEFENDED"
        elif breached_after:
            counts["breached_after_maturity_before_defense"] += 1; rec["terminal"] = "BREACHED_BEFORE_DEFENSE_CONFIRMATION"
        else:
            counts["expired_without_defense"] += 1; rec["terminal"] = "EXPIRED_WITHOUT_DEFENSE"
        rec["approach_windows"] = approaches; rec["windows_without_rejection"] = windows_no_rejection; records.append(rec)
    output.sort(key=lambda v: (v.eligibility_ns, v.side, v.price))
    production = core._qualify_levels(raw, candidates=candidates, atr=atr, config=cfg)
    assert [asdict(v) for v in output] == [asdict(v) for v in production], "qualification audit diverged from production"
    return output, counts, records


def diagnose(*, config_path: Path, input_root: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text())
    cfg = core.MatureSwingBreakoutConfig.from_mapping(config["scenario"])
    costs = CostConfig.from_mapping(config["costs"])
    start = pd.Timestamp(datetime.fromisoformat(config["validation"]["first_week_start"]).replace(tzinfo=UTC))
    end = start + pd.Timedelta(days=7)
    npz, cols, raw_dir = _resolve(input_root)
    features = load_feature_matrix(npz, cols); raw_all = load_raw_one_minute(raw_dir)
    state = core.build_state(features, cfg)
    raw_view = core._normalise_index(raw_all[["open", "high", "low", "close"]])
    x = state.join(raw_view.rename(columns={"open":"raw_open","high":"raw_high","low":"raw_low","close":"raw_close"}), how="inner")
    atr = core._true_range(raw_view).rolling(cfg.atr_lookback_minutes, min_periods=max(30,cfg.atr_lookback_minutes//2)).median().shift(1)
    x["atr"] = atr.reindex(x.index); x["body"] = (x["raw_close"]-x["raw_open"]).abs()
    x["body_threshold"] = x["body"].rolling(cfg.prior_window_minutes,min_periods=cfg.prior_minimum_minutes).quantile(cfg.displacement_body_quantile).shift(1)
    candidates = core._generate_swing_candidates(raw_view, config=cfg)
    levels, qualification, qrecords = _qualification_audit(raw_view, candidates, atr, cfg)
    counts: dict[str, int] = {
        "evaluation_minutes": 0, "finite_event_minutes": 0, "minutes_with_active_level": 0,
        "unique_active_levels": 0, "pre_evaluation_consumed_levels": 0,
        "one_sided_first_breach_events": 0, "dual_side_breach_events": 0, "breached_levels_consumed": 0,
        "event_extension_pass": 0, "turnover_pass": 0, "classification_complete": 0,
        "outside_close_count_pass": 0, "final_acceptance_distance_pass": 0, "spot_outside_pass": 0,
        "spot_ratio_pass": 0, "basis_share_pass": 0, "common_acceptance_pass": 0,
        "displacement_fvg_pass": 0, "retrace_rows_examined": 0, "retrace_invalidated": 0,
        "fvg_touch": 0, "fvg_midpoint_rejection": 0, "old_level_held": 0, "retrace_flow_pass": 0,
        "target_pool_nonempty": 0, "target_geometry_cost_pass": 0, "production_signals": 0,
    }
    consumed: set[str] = set(); start_ns = int(start.value); active_ids: set[str] = set(); events: list[dict[str,Any]] = []
    for level in levels:
        if not (level.eligibility_ns < start_ns <= level.expiry_ns): continue
        hist = raw_view.loc[(raw_view.index.asi8 > level.eligibility_ns) & (raw_view.index.asi8 < start_ns)]; ha = atr.reindex(hist.index)
        if any(math.isfinite(float(ha.iloc[i])) and float(ha.iloc[i])>0 and core._breached(level,row,float(ha.iloc[i]),cfg.minimum_level_breach_atr) for i,(_,row) in enumerate(hist.iterrows())): consumed.add(level.level_id)
    counts["pre_evaluation_consumed_levels"] = len(consumed)
    event_fields=("raw_high","raw_low","raw_close","atr","aggressive_total_quote_1m","turnover_threshold","spot_close","perp_spot_log_basis")
    eval_positions=[int(x.index.get_loc(ts)) for ts in x.loc[(x.index>=start)&(x.index<end)].index]; counts["evaluation_minutes"] = len(eval_positions)
    for ep in eval_positions:
        if ep<1: continue
        ts=pd.Timestamp(x.index[ep]); ns=int(ts.value); ev=x.iloc[ep]; prev=x.iloc[ep-1]
        if not _finite(ev,event_fields) or not _finite(prev,("raw_close","perp_spot_log_basis")): continue
        counts["finite_event_minutes"] += 1; av=float(ev["atr"])
        if av<=0: continue
        active=[l for l in levels if l.level_id not in consumed and l.eligibility_ns < ns <= l.expiry_ns]
        if not active: continue
        counts["minutes_with_active_level"] += 1; active_ids.update(l.level_id for l in active); pc=float(prev["raw_close"])
        upper=[l for l in active if l.side=="HIGH" and pc<=l.price and float(ev["raw_high"])>=l.price+cfg.minimum_level_breach_atr*av]
        lower=[l for l in active if l.side=="LOW" and pc>=l.price and float(ev["raw_low"])<=l.price-cfg.minimum_level_breach_atr*av]
        if upper and lower:
            counts["dual_side_breach_events"] += 1; consumed.update(l.level_id for l in upper+lower); counts["breached_levels_consumed"] += len(upper)+len(lower); continue
        breached=upper or lower
        if not breached: continue
        counts["one_sided_first_breach_events"] += 1; direction=1 if upper else -1; consumed.update(l.level_id for l in breached); counts["breached_levels_consumed"] += len(breached)
        clusters=core._cluster_breached(breached,tolerance=cfg.level_merge_atr*av); cluster=clusters[-1] if direction>0 else clusters[0]
        boundary=max(l.price for l in cluster) if direction>0 else min(l.price for l in cluster); extreme=float(ev["raw_high"] if direction>0 else ev["raw_low"]); extension=direction*(extreme-boundary)
        rec={"event_utc":ts.isoformat(),"direction":"UP" if direction>0 else "DOWN","breached_level_ids":[l.level_id for l in breached],"boundary":boundary,"event_extension_atr":extension/av}
        if extension>cfg.maximum_event_extension_atr*av: rec["terminal"]="EVENT_OVEREXTENDED"; events.append(rec); continue
        counts["event_extension_pass"] += 1; rec["turnover_ratio"] = float(ev["aggressive_total_quote_1m"])/max(float(ev["turnover_threshold"]),1e-12)
        if float(ev["aggressive_total_quote_1m"])<float(ev["turnover_threshold"]): rec["terminal"]="TURNOVER_FAIL"; events.append(rec); continue
        counts["turnover_pass"] += 1; ce=min(ep+cfg.classification_minutes-1,len(x)-1); seg=x.iloc[ep:ce+1]; seg=seg.loc[seg.index<end]
        if len(seg)<cfg.classification_minutes: rec["terminal"]="INCOMPLETE_CLASSIFICATION"; events.append(rec); continue
        counts["classification_complete"] += 1; outside=seg["raw_close"]>boundary if direction>0 else seg["raw_close"]<boundary; last=seg.iloc[-1]
        fp=float(last["raw_close"]); fs=float(last["spot_close"]); pb=float(prev["perp_spot_log_basis"]); fb=float(last["perp_spot_log_basis"]); sb=boundary/math.exp(pb)
        fdist=direction*(fp-boundary); sdist=direction*(fs-sb); pe=max(direction*(fp/boundary-1),1e-12); se=direction*(fs/sb-1); sr=se/pe; bs=max(direction*(fb-pb),0)/pe
        rec.update({"outside_closes":int(outside.sum()),"final_acceptance_atr":fdist/av,"spot_outside":sdist>0,"spot_ratio":sr,"basis_share":bs})
        if int(outside.sum())>=cfg.minimum_outside_closes: counts["outside_close_count_pass"]+=1
        if fdist>=cfg.minimum_acceptance_atr*av: counts["final_acceptance_distance_pass"]+=1
        if sdist>0: counts["spot_outside_pass"]+=1
        if sr>=cfg.minimum_spot_acceptance_ratio: counts["spot_ratio_pass"]+=1
        if bs<=cfg.maximum_basis_expansion_share: counts["basis_share_pass"]+=1
        accepted=int(outside.sum())>=cfg.minimum_outside_closes and fdist>=cfg.minimum_acceptance_atr*av and sdist>0 and sr>=cfg.minimum_spot_acceptance_ratio and bs<=cfg.maximum_basis_expansion_share
        if not accepted: rec["terminal"]="COMMON_ACCEPTANCE_FAIL"; events.append(rec); continue
        counts["common_acceptance_pass"]+=1; disp=core._find_displacement(x=x,event_position=ce,boundary=boundary,direction=direction,config=cfg)
        if disp is None: rec["terminal"]="DISPLACEMENT_FVG_FAIL"; events.append(rec); continue
        counts["displacement_fvg_pass"]+=1; dp,fl,fh=disp; rec.update({"displacement_utc":pd.Timestamp(x.index[dp]).isoformat(),"fvg_low":fl,"fvg_high":fh}); re=min(dp+cfg.retrace_minutes,len(x)-1)
        tb=core._completed_bars(raw_view,start=ts-pd.Timedelta(minutes=cfg.target_lookback_minutes),end=ts-pd.Timedelta(minutes=1),minutes=cfg.swing_bar_minutes); ph,pl=core._intact_pivots(tb,cfg.swing_radius_bars); terminal="NO_EXECUTABLE_RETRACE"
        for p in range(dp+1,re+1):
            observed=pd.Timestamp(x.index[p])
            if observed>=end: break
            row=x.iloc[p]
            if not _finite(row,("raw_high","raw_low","raw_close","signed_flow_ratio_1m","atr")): continue
            counts["retrace_rows_examined"]+=1; inv=boundary-cfg.invalidation_inside_atr*float(row["atr"]) if direction>0 else boundary+cfg.invalidation_inside_atr*float(row["atr"])
            if (float(row["raw_low"])<=inv if direction>0 else float(row["raw_high"])>=inv): counts["retrace_invalidated"]+=1; terminal="RETRACE_INVALIDATED"; break
            touched=float(row["raw_high"])>=fl and float(row["raw_low"])<=fh
            if not touched: continue
            counts["fvg_touch"]+=1; mid=.5*(fl+fh); rejected=float(row["raw_close"])>=mid if direction>0 else float(row["raw_close"])<=mid
            if not rejected: terminal="FVG_TOUCH_NO_MIDPOINT_REJECTION"; continue
            counts["fvg_midpoint_rejection"]+=1; held=float(row["raw_close"])>boundary if direction>0 else float(row["raw_close"])<boundary
            if not held: terminal="OLD_LEVEL_NOT_HELD"; continue
            counts["old_level_held"]+=1; flow=direction*float(row["signed_flow_ratio_1m"])
            if flow<cfg.minimum_retrace_flow_alignment: terminal="RETRACE_FLOW_FAIL"; continue
            counts["retrace_flow_pass"]+=1; entry=float(row["raw_close"]); side="BUY" if direction>0 else "SELL"; path=x.iloc[ep:p+1]; path_ext=float(path["raw_high"].max()) if side=="BUY" else float(path["raw_low"].min()); intact=[v for v in ph if v>path_ext] if side=="BUY" else [v for v in pl if v<path_ext]
            if intact: counts["target_pool_nonempty"]+=1
            sel=core._select_nearest_target(levels=intact,side=side,entry=entry,stop=inv,costs=costs,minimum_rr=cfg.minimum_target_cost_after_rr,maximum_rr=cfg.maximum_target_cost_after_rr)
            if sel is None: terminal="TARGET_GEOMETRY_OR_COST_FAIL"; continue
            counts["target_geometry_cost_pass"]+=1; terminal="SIGNAL"; rec.update({"retest_utc":observed.isoformat(),"entry":entry,"stop":inv,"target":sel[0],"cost_after_rr":sel[1]}); break
        rec["terminal"]=terminal; events.append(rec)
    counts["unique_active_levels"] = len(active_ids)
    production=core.build_rotation_signals(state=state,raw=raw_all,evaluation_start=start,evaluation_end=end,config=cfg,costs=costs); counts["production_signals"] = len(production)
    assert counts["target_geometry_cost_pass"] == len(production), (counts["target_geometry_cost_pass"],len(production))
    return {"candidate":config["candidate"],"evaluation_start_utc":start.isoformat(),"evaluation_end_utc":end.isoformat(),"require_defense_memory":cfg.require_defense_memory,"qualification":qualification,"scenario":counts,"first_breach_events":events,"qualification_records":qrecords,"production_signal_count":len(production),"diagnostic_only":True,"custom_backtest_engine":False}


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--config",type=Path,required=True); p.add_argument("--input-root",type=Path,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    result=diagnose(config_path=a.config,input_root=a.input_root); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n"); print(json.dumps({"qualification":result["qualification"],"scenario":result["scenario"]},indent=2,sort_keys=True)); return 0

if __name__=="__main__": raise SystemExit(main())
