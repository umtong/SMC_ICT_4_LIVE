#!/usr/bin/env python3
"""Leakage-free event/plan router for hierarchical event-time auctions."""
from __future__ import annotations

from pathlib import Path
import math

import numpy as np
import pandas as pd

import sequential_commitment_router_clean as core

RISK = core.core.RISK if hasattr(core, "core") else 0.03


def period_name(directory: Path) -> str:
    for token in ("dev-", "cal-", "fresh-", "eval-", "holdout-"):
        at = directory.name.find(token)
        if at >= 0:
            return directory.name[at:]
    return directory.name


def load_actions(root: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(root.rglob("departure_actions.csv.gz")):
        frame = pd.read_csv(path, low_memory=False)
        frame["period"] = period_name(path.parent)
        frames.append(frame)
    if not frames:
        raise RuntimeError(f"no event-time actions below {root}")
    frame = pd.concat(frames, ignore_index=True, sort=False)
    for column in (
        "order_time_ns", "departure_time_ns", "order_terminal_time_ns",
        "fill_time_ns", "resolution_time_ns", "gross_rr",
        "planned_target_net_r", "actual_target_net_r", "net_r",
        "holding_minutes",
    ):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["filled"] = frame.fill_state.astype(str).str.startswith("FILLED")
    frame["resolved"] = frame.outcome.astype(str).isin(
        ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE"]
    )
    frame["win"] = frame.outcome.astype(str).eq("TARGET_FIRST")
    ambiguous = frame.outcome.astype(str).str.startswith("AMBIGUOUS")
    frame.loc[ambiguous & frame.net_r.isna(), "net_r"] = -1.0
    frame["target_net_r"] = pd.to_numeric(frame.planned_target_net_r, errors="coerce")
    frame["terminal_ns"] = pd.to_numeric(frame.order_terminal_time_ns, errors="coerce")
    frame["realized_log"] = np.where(
        frame.resolved & frame.net_r.notna(),
        np.log1p(np.clip(0.03 * frame.net_r.astype(float), -0.99, None)),
        np.where(~frame.filled, 0.0, np.nan),
    )
    return core.core.add_stopping_labels(frame) if hasattr(core, "core") else core.add_stopping_labels(frame)


# sequential_commitment_router_clean patches the actual core module; use that module
# directly so its clean feature lineage and global account router remain active.
router = core.core if hasattr(core, "core") else core
router.load_actions = load_actions

if __name__ == "__main__":
    router.main()
