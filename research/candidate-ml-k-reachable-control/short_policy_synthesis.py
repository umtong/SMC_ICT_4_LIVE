#!/usr/bin/env python3
"""Synthesize scenario families into one short-horizon account policy.

The candidate masks below are not unrelated parameter grids.  Each expresses a
complete causal interpretation seen repeatedly across the research branches:
accepted control followed by first mitigation, failed-auction reclaim with local
ownership, and a combined router that arbitrates them in one account.  A policy
is ranked only with development periods; fresh periods are reported unchanged.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = REPO_ROOT / "research/candidate-liquidity-episode-policy-v1"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
import route_episode_policy as base  # noqa: E402

RISK = 0.03
EPS = 1e-12
RESOLVED = base.RESOLVED_OUTCOMES


def num(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").fillna(default)


def txt(frame: pd.DataFrame, name: str, default: str = "") -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=object)
    return frame[name].fillna(default).astype(str)


def candidate_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    ready = frame.causal_models_ready.fillna(False).astype(bool)
    first = num(frame, "feature__first_defended_return").ge(1.0)
    owned = num(frame, "feature__local_control_ownership").gt(0.0)
    coherent = num(frame, "feature__causal_control_coherence").gt(0.0)
    contradiction = num(frame, "feature__contradiction_state").ge(0.5)
    reachable = num(frame, "reachable_frontier_prior").ge(0.24)
    p_target = num(frame, "p_target_conservative", np.nan)
    p_fill = num(frame, "p_fill_conservative", num(frame, "p_fill", 0.0))
    edge = num(frame, "probability_edge")
    growth = num(frame, "expected_log_growth")
    support = num(frame, "target_support")
    family = txt(frame, "family")
    route = txt(frame, "route_class", txt(frame, "route_kind")).str.upper()
    context = txt(frame, "context_class").str.upper()
    residual = num(frame, "feature__relative_return_signed")
    base_quality = (
        ready & first & owned & coherent & ~contradiction & reachable
        & support.ge(12) & p_target.gt(0.52) & edge.gt(0.04)
        & growth.gt(0.0) & p_fill.gt(0.05)
        & num(frame, "gross_rr").ge(1.0)
    )
    accepted = family.eq("ACCEPTED_AUCTION_CONTINUATION")
    mitigation = family.eq("INITIATIVE_MITIGATION_CONTINUATION")
    failed = family.eq("FAILED_AUCTION_REVERSAL")
    local_route = route.str.contains("LOCAL", regex=False)
    nonopposed = ~context.eq("OPPOSED")
    reclaim_owned = failed & residual.ge(-0.01) & local_route
    continuation = (accepted | mitigation) & nonopposed
    return {
        "all_evidence_supported": base_quality,
        "continuation_first_mitigation": base_quality & continuation,
        "locally_owned_reclaim": base_quality & reclaim_owned,
        "local_completion_only": base_quality & local_route,
        "integrated_control_transfer": base_quality & (continuation | reclaim_owned),
    }


def route(scored: pd.DataFrame, mask: pd.Series):
    work = scored.copy()
    work["policy_eligible"] = mask.fillna(False)
    return base.route_account(work)


def metric(frame: pd.DataFrame, period_days: dict[str, int]) -> dict[str, Any]:
    closed = frame[
        pd.to_numeric(frame.get("net_r"), errors="coerce").notna()
        & txt(frame, "outcome").isin(RESOLVED)
    ].copy()
    closed["net_r"] = pd.to_numeric(closed.net_r, errors="coerce")
    nav = peak = 1.0
    mdd = 0.0
    for value in closed.sort_values(["order_time", "episode_id"]).net_r.astype(float):
        nav *= max(EPS, 1.0 + RISK * value)
        peak = max(peak, nav)
        mdd = max(mdd, 1.0 - nav / peak)
    periods = frame.period.astype(str).unique().tolist() if len(frame) else []
    days = int(sum(period_days.get(p, 0) for p in periods))
    wins = txt(closed, "outcome").eq("TARGET_FIRST")
    return {
        "orders": int(len(frame)),
        "closed_trades": int(len(closed)),
        "calendar_days": days,
        "trades_per_day": float(len(closed) / max(days, 1)),
        "target_first_rate": float(wins.mean()) if len(closed) else None,
        "mean_net_r": float(closed.net_r.mean()) if len(closed) else None,
        "mean_gross_rr": float(num(closed, "gross_rr", np.nan).mean()) if len(closed) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(mdd),
    }


def by_period(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows=[]
    closed=frame[pd.to_numeric(frame.get('net_r'),errors='coerce').notna()].copy()
    closed['net_r']=pd.to_numeric(closed.net_r,errors='coerce')
    closed['win']=txt(closed,'outcome').eq('TARGET_FIRST')
    for period,g in closed.groupby('period',sort=True):
        rows.append({'period':str(period),'trades':int(len(g)),
          'target_first_rate':float(g.win.mean()),'mean_net_r':float(g.net_r.mean()),
          'mean_gross_rr':float(num(g,'gross_rr',np.nan).mean())})
    return rows


def development_rank(rows: list[dict[str, Any]], days: int) -> tuple[float, ...]:
    if not rows:
        return (-1e9, -1e9, -1e9, -1e9)
    means=[float(row['mean_net_r']) for row in rows if row['trades']]
    wins=[float(row['target_first_rate']) for row in rows if row['trades']]
    trades=sum(int(row['trades']) for row in rows)
    if not means:
        return (-1e9, -1e9, -1e9, -1e9)
    # Prefer logic that remains useful in the weakest development regime, then
    # accuracy and opportunity.  No fresh result participates in this ordering.
    return (min(means), float(np.median(means)), float(np.median(wins)), trades/max(days,1))


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--scored',type=Path,required=True)
    parser.add_argument('--source-summary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    scored=pd.read_csv(args.scored,low_memory=False)
    scored['order_time']=pd.to_datetime(pd.to_numeric(scored.order_time_ns,errors='coerce'),unit='ns',utc=True,errors='coerce')
    source=json.loads(args.source_summary.read_text())
    period_days={str(k):int(v) for k,v in source.get('period_days',{}).items()}
    masks=candidate_masks(scored)
    result={'selection_uses_fresh_results':False,'policies':{}}
    outputs=[]; ranks=[]
    for name,mask in masks.items():
        orders,closed,rejected,account=route(scored,mask)
        orders['synthesis_policy']=name; closed['synthesis_policy']=name
        outputs.append(closed)
        dev=closed[txt(closed,'role').eq('dev')]
        fresh=closed[txt(closed,'role').eq('fresh')]
        dev_rows=by_period(dev); fresh_rows=by_period(fresh)
        dev_days=sum(period_days.get(p,0) for p in scored.loc[txt(scored,'role').eq('dev'),'period'].astype(str).unique())
        ranks.append((development_rank(dev_rows,dev_days),name))
        result['policies'][name]={
          'development':metric(dev,period_days),'fresh':metric(fresh,period_days),
          'development_by_period':dev_rows,'fresh_by_period':fresh_rows,
          'account_router':account,
        }
    ranks.sort(reverse=True)
    result['development_selected_policy']=ranks[0][1] if ranks else None
    result['development_ranking']=[{'policy':name,'rank':list(rank)} for rank,name in ranks]
    selected_name=result['development_selected_policy']
    selected_closed=pd.concat(outputs,ignore_index=True,sort=False)
    selected_closed.to_csv(args.output/'all_policy_closed_trades.csv',index=False)
    selected_closed[selected_closed.synthesis_policy.eq(selected_name)].to_csv(
      args.output/'development_selected_policy_trades.csv',index=False)
    (args.output/'summary.json').write_text(json.dumps(result,indent=2,sort_keys=True,default=str)+'\n')
    print(json.dumps(result,indent=2,sort_keys=True,default=str))

if __name__=='__main__':
    main()
