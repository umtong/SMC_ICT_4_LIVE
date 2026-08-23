#!/usr/bin/env python3
"""Route hierarchical directional-change auction plans through one account."""
from __future__ import annotations

from pathlib import Path
import pandas as pd

import strict_causal_global_router as core


def period_name(directory: Path) -> str:
    for token in ("dev-", "cal-", "fresh-", "eval-", "holdout-"):
        at = directory.name.find(token)
        if at >= 0:
            return directory.name[at:]
    return directory.name


def load_actions(root: Path) -> pd.DataFrame:
    frames=[]
    for path in sorted(root.rglob("event_time_actions.csv.gz")):
        frame=pd.read_csv(path,low_memory=False);frame["period"]=period_name(path.parent);frames.append(frame)
    if not frames:raise RuntimeError(f"no event-time actions below {root}")
    frame=pd.concat(frames,ignore_index=True,sort=False)
    for column in ("order_time_ns","order_terminal_time_ns","fill_time_ns","resolution_time_ns","gross_rr","planned_target_net_r","actual_target_net_r","net_r","holding_minutes"):
        if column in frame:frame[column]=pd.to_numeric(frame[column],errors="coerce")
    frame["filled"]=frame.fill_state.astype(str).str.startswith("FILLED")
    frame["resolved"]=frame.outcome.astype(str).isin(["TARGET_FIRST","STOP_FIRST","AMBIGUOUS_SAME_MINUTE","AMBIGUOUS_FILL_BARRIER_SAME_MINUTE"])
    frame["win"]=frame.outcome.astype(str).eq("TARGET_FIRST")
    frame.loc[frame.outcome.astype(str).str.startswith("AMBIGUOUS") & frame.net_r.isna(),"net_r"]=-1.0
    frame["target_net_r"]=frame.actual_target_net_r.where(frame.actual_target_net_r.notna(),frame.planned_target_net_r)
    frame["terminal_ns"]=pd.to_numeric(frame.order_terminal_time_ns,errors="coerce")
    return frame


def economic_lattice(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[
        frame.family.astype(str).isin(["EXTERNAL_FAILED_AUCTION","HIERARCHICAL_CONTINUATION"])
        & frame.entry_geometry.astype(str).isin(["ZONE_PROXIMAL_LIMIT","ZONE_MID_LIMIT"])
        & frame.gross_rr.astype(float).between(1.0,2.0)
        & frame.target_net_r.astype(float).ge(0.30)
    ].copy()


core.load_actions=load_actions
core.economic_lattice=economic_lattice

if __name__=="__main__":core.main()
