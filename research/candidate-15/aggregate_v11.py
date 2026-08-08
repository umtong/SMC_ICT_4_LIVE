#!/usr/bin/env python3
"""Aggregate V11 beta diffusion plus completed-source auction evidence."""
from __future__ import annotations
import argparse, json
from collections import Counter
from math import isfinite
from pathlib import Path
from typing import Any
from aggregate import aggregate as aggregate_base, read_object, write_object
from aggregate_v10 import _management_audit

BETA='BETA_COHERENT_DIFFUSION_LAG_MSS_FVG'
FAR='COMPLETED_SOURCE_FAILED_AUCTION'
AAC='COMPLETED_SOURCE_ACCEPTED_AUCTION'
ALLOWED={BETA,FAR,AAC}
SYMS={'BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT'}
H={'24','48','96','192'}

def _base(root: Path)->dict[str,Any]:
    live=root/'protocol.json'; v11=root/'protocol-v11.json'; old=live.read_bytes()
    try:
        live.write_bytes(v11.read_bytes())
        return aggregate_base(root)
    finally:
        live.write_bytes(old)

def _beta_ok(plan:dict[str,Any])->bool:
    d=plan.get('details',{}) if isinstance(plan.get('details'),dict) else {}
    o=d.get('candidate15_v9_ownership'); t=d.get('candidate15_v9_transfer')
    if not isinstance(o,dict) or not isinstance(t,dict): return False
    accepted=set(o.get('accepted_symbols',()))
    betas=t.get('beta_zero_intercept_by_horizon',{}); sg=t.get('state_delivery_gap_by_horizon',{}); gg=t.get('geometry_delivery_gap_by_horizon',{})
    try: ratio=float(t.get('residual_to_weakest_sender_body_ratio')); cr=float(t.get('completion_costed_r'))
    except (TypeError,ValueError): return False
    symbol=str(plan.get('symbol',''))
    return (
      plan.get('module')==BETA and d.get('module')==BETA and d.get('route')=='BETA_COHERENT_DIFFUSION_LAG'
      and len(accepted)==3 and accepted.issubset(SYMS) and SYMS-accepted=={symbol} and o.get('laggard_symbol')==symbol
      and set(map(str,betas))==H and all(float(v)>0 for v in betas.values())
      and set(map(str,sg))==H and all(float(v)>0 for v in sg.values())
      and set(map(str,gg))==H and all(float(v)>0 for v in gg.values())
      and isfinite(ratio) and .5<=ratio<1 and isfinite(cr) and cr>0
      and t.get('estimation_cutoff')=='STRICTLY_BEFORE_FIRST_EVIDENCE_EVENT'
    )

def _completed_ok(plan:dict[str,Any], family:str)->bool:
    d=plan.get('details',{}) if isinstance(plan.get('details'),dict) else {}
    meta=d.get('candidate15_v11_family')
    common=all(d.get(k) is not None for k in ('pool_source','range_id','sweep_ts_ns','zone_low','zone_high'))
    no_rearm=not any(x in json.dumps(d,sort_keys=True) for x in ('REARM_AFTER_MISSED_RETRACE','rearmed_parent','V17_REARM'))
    base=(plan.get('module')==family and d.get('module')==family and d.get('route')==family and isinstance(meta,dict)
          and meta.get('family')==family and meta.get('first_plan_only') is True and common and no_rearm)
    if family==FAR:
        return base and plan.get('scenario')=='FAR' and d.get('entry_model')=='FAR_CAUSAL_DISPLACEMENT_RETRACE_LIMIT' and d.get('stop_model')=='SWEEP_EXTREME_STRUCTURAL_INVALIDATION' and d.get('v15_market_chase_disabled') is True and d.get('v15_void_stop_disabled') is True
    return base and plan.get('scenario')=='AAC' and d.get('defended_pullback') is not None and d.get('source_boundary') is not None

def _route_audit(root:Path,p:dict[str,Any])->dict[str,Any]:
    counts=Counter(); violations=[]; scenarios=Counter(); symbols=Counter()
    for interval in p['selection']['intervals']:
        plans=read_object(root/'results'/interval/'submitted_plans.json').get('plans',[])
        for plan in plans:
            d=plan.get('details',{}) if isinstance(plan.get('details'),dict) else {}
            fam=str(plan.get('module') or d.get('module') or '')
            counts[fam]+=1; scenarios[str(plan.get('scenario'))]+=1; symbols[str(plan.get('symbol'))]+=1
            ok=(fam==BETA and _beta_ok(plan)) or (fam in {FAR,AAC} and _completed_ok(plan,fam))
            if not ok: violations.append({'interval':interval,'scenario_id':plan.get('scenario_id'),'family':fam,'scenario':plan.get('scenario'),'details':d})
    return {'family_counts':dict(sorted(counts.items())),'scenario_counts':dict(sorted(scenarios.items())),'symbol_counts':dict(sorted(symbols.items())),'violation_count':len(violations),'violations':violations[:25],'beta_plans':counts[BETA],'completed_auction_plans':counts[FAR]+counts[AAC]}

def aggregate(root:Path)->dict[str,Any]:
    p=read_object(root/'protocol-v11.json'); payload=_base(root); route=_route_audit(root,p); mgmt=_management_audit(root,p)
    checks=dict(payload.get('checks',{})); checks.pop('only_response_continuation_submitted',None)
    checks['route_integrity']=route['violation_count']==0 and bool(route['family_counts']) and set(route['family_counts']).issubset(ALLOWED)
    checks['both_independent_families_observed']=route['beta_plans']>0 and route['completed_auction_plans']>0
    checks['beta_management_integrity']=(mgmt['completed_without_action_count']==0 and mgmt['fail_closed_count']==0 and mgmt['cost_cover_contract_violation_count']==0)
    if not checks.get('minimum_closed_trades') or not checks.get('minimum_active_intervals'):
        cls='CANDIDATE15_V11_INSUFFICIENT_ACTIVITY'
    elif all(checks.values()): cls='CANDIDATE15_V11_DEVELOPMENT_PROMISING'
    else: cls='CANDIDATE15_V11_DEVELOPMENT_REJECTED'
    payload.update(schema='candidate-15-v11-family-integration-aggregate-v1',candidate=p['candidate'],protocol=p['schema'],classification=cls,checks=checks,family_route_audit=route,beta_management_audit=mgmt,success_claim=False)
    write_object(root/'aggregate.json',payload)
    lines=['# Candidate 15 V11 beta plus completed-source auction router','',f'**{cls}**','',f"- weekly_reset_nav_multiple: `{payload.get('weekly_reset_nav_multiple')}`",f"- daily_geometric_growth: `{payload.get('daily_geometric_growth')}`",f"- closed_trades: `{payload.get('closed_trades')}`",f"- wins / losses: `{payload.get('wins')} / {payload.get('losses')}`",f"- win_rate: `{payload.get('win_rate')}`",f"- payoff_ratio: `{payload.get('payoff_ratio')}`",f"- active_intervals: `{payload.get('active_intervals')}`",f"- family_counts: `{route['family_counts']}`",f"- route_violations: `{route['violation_count']}`",f"- cost_cover_contract_violations: `{mgmt['cost_cover_contract_violation_count']}`",'', '## Checks']
    lines += [f'- {k}: `{v}`' for k,v in checks.items()]
    lines += ['', 'E01-E06 are exposed integration-development intervals; success_claim remains false.']
    (root/'RESULT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return payload

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,default=Path(__file__).resolve().parent); a=ap.parse_args(); print(json.dumps(aggregate(a.root.resolve()),indent=2,sort_keys=True,default=str)); return 0
if __name__=='__main__': raise SystemExit(main())
