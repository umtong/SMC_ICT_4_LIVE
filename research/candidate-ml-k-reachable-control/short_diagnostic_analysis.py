#!/usr/bin/env python3
"""Compact logic diagnosis for ML-k short-horizon harvested episodes.

This file is deliberately about the trading decision, not a new validation
bureaucracy.  It compares a small number of causal interpretations already
suggested by the branch history: first defended return, local control ownership,
continuation versus reclaim, and target reachability.  Outcome columns are used
only after a plan has been declared, never to create a decision feature.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any, Callable

import numpy as np
import pandas as pd

RISK = 0.03
EPS = 1e-12
PERIOD = re.compile(r"(?:dev|fresh)-\d{4}-[a-z0-9-]+", re.I)
RESOLVED = {
    "TARGET_FIRST",
    "STOP_FIRST",
    "AMBIGUOUS_SAME_MINUTE",
    "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
}


def num(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").fillna(default)


def text(frame: pd.DataFrame, name: str, default: str = "") -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=object)
    return frame[name].fillna(default).astype(str)


def truth(frame: pd.DataFrame, name: str) -> pd.Series:
    return text(frame, name).str.lower().isin({"true", "1", "yes"})


def period_from(path: Path, summary: dict[str, Any]) -> str:
    value = str(summary.get("period", ""))
    if value:
        return value
    for part in reversed(path.parts):
        match = PERIOD.search(part)
        if match:
            return match.group(0)
    return path.parent.name


def load(root: Path) -> tuple[pd.DataFrame, dict[str, int]]:
    frames: list[pd.DataFrame] = []
    days: dict[str, int] = {}
    for path in sorted(root.rglob("departure_actions.csv.gz")):
        summary_path = path.parent / "summary.json"
        summary = (
            json.loads(summary_path.read_text(encoding="utf-8"))
            if summary_path.exists()
            else {}
        )
        period = period_from(path, summary)
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        frame = frame.copy()
        frame["period"] = period
        frame["role"] = "fresh" if period.startswith("fresh-") else "dev"
        frames.append(frame)
        if summary.get("start") and summary.get("end"):
            start, end = pd.Timestamp(summary["start"]), pd.Timestamp(summary["end"])
            days[period] = max(1, int((end - start).days))
    if not frames:
        raise FileNotFoundError(f"No departure_actions.csv.gz below {root}")
    output = pd.concat(frames, ignore_index=True, sort=False)
    output = output[truth(output, "order_exists")].copy()
    output["order_time"] = pd.to_datetime(
        num(output, "order_time_ns", np.nan), unit="ns", utc=True, errors="coerce"
    )
    output["terminal_time"] = pd.to_datetime(
        num(output, "order_terminal_time_ns", np.nan),
        unit="ns",
        utc=True,
        errors="coerce",
    )
    output["filled"] = num(output, "fill_time_ns", np.nan).notna()
    output["resolved"] = text(output, "outcome").isin(RESOLVED)
    output["win"] = text(output, "outcome").eq("TARGET_FIRST")
    output["net_r_num"] = num(output, "net_r", np.nan)
    return output.reset_index(drop=True), days


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    geometry = text(out, "entry_geometry").str.upper()
    state = (
        text(out, "state") + "|" + text(out, "state_id") + "|" + text(out, "route_state")
    ).str.upper()
    family = text(out, "family").str.upper()
    out["is_first_return"] = (
        geometry.str.contains("FIRST|RETEST|MITIGATION", regex=True)
        | state.str.contains("FIRST_RETEST|MITIGATION", regex=True)
        | geometry.str.contains("OB_FVG_OVERLAP|SOURCE_OVERLAP|TRANSFERRED_SOURCE", regex=True)
    )
    out["is_overlap_entry"] = geometry.str.contains("OVERLAP", regex=False)
    out["is_accepted"] = family.eq("ACCEPTED_AUCTION_CONTINUATION")
    out["is_failed"] = family.eq("FAILED_AUCTION_REVERSAL")
    out["is_mitigation"] = family.eq("INITIATIVE_MITIGATION_CONTINUATION")

    control = (
        0.38 * num(out, "control_path_efficiency")
        + 0.24 * np.tanh(num(out, "control_move_atr"))
        + 0.18 * np.tanh(num(out, "control_flow_share_signed") * 3.0)
        + 0.20 * np.tanh(num(out, "control_effort_result"))
    )
    context = (
        0.36 * num(out, "ctx_structure_vote")
        + 0.28 * num(out, "ctx_momentum_vote")
        + 0.20 * num(out, "ctx_breadth_vote")
        + 0.16 * num(out, "ctx_structure_agreement")
    )
    common = (
        0.50 * num(out, "common_factor_signed")
        + 0.50 * num(out, "common_breadth_signed")
    )
    residual = num(out, "relative_return_signed")
    out["logic_local_control"] = control
    out["logic_context"] = context
    out["logic_common"] = common
    out["logic_residual"] = residual
    # Local ownership must not be manufactured solely by a broad market impulse.
    out["logic_ownership"] = control + 0.35 * residual - 0.20 * np.maximum(common, 0.0)
    out["logic_coherence"] = (
        0.55 * num(out, "mechanism_coherence")
        + 0.30 * out["logic_ownership"]
        + 0.15 * context
    )
    rr = num(out, "gross_rr", np.nan)
    out["rr_band"] = pd.cut(
        rr,
        [-np.inf, 1.25, 1.75, 2.5, 4.0, np.inf],
        labels=["1.00-1.25", "1.25-1.75", "1.75-2.50", "2.50-4.00", "4.00+"],
    ).astype(str)
    out["target_reachability"] = (
        np.exp(-0.42 * np.maximum(rr - 1.35, 0.0))
        * (0.76 + 0.24 * out.is_first_return.astype(float))
        * (0.82 + 0.18 * np.tanh(out.logic_coherence))
    )
    return out


def policy_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    gross = num(frame, "gross_rr")
    structural = gross.ge(1.0) & num(frame, "planned_target_net_r").gt(0.0)
    first = frame.is_first_return
    owned = frame.logic_ownership.gt(0.0)
    coherent = frame.logic_coherence.gt(0.0)
    aligned = frame.logic_context.ge(-0.05)
    reachable = frame.target_reachability.ge(0.34)
    return {
        "all_structural": structural,
        "first_defended_return": structural & first,
        "owned_first_return": structural & first & owned,
        "accepted_owned_first_return": structural & first & owned & coherent & aligned & frame.is_accepted,
        "failed_owned_reclaim": structural & first & owned & coherent & frame.is_failed,
        "coherent_combined": structural & first & owned & coherent & reachable & (
            (frame.is_accepted & aligned)
            | (frame.is_mitigation & aligned)
            | (frame.is_failed & frame.logic_residual.ge(-0.02))
        ),
    }


def route(frame: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    candidates = frame[mask.fillna(False)].copy()
    if candidates.empty:
        return candidates
    candidates = candidates.sort_values(
        ["period", "order_time", "logic_coherence", "target_reachability", "gross_rr", "episode_id"],
        ascending=[True, True, False, False, False, True],
    )
    selected: list[pd.Series] = []
    for _, group in candidates.groupby("period", sort=True):
        busy_until = pd.Timestamp.min.tz_localize("UTC")
        used: set[str] = set()
        for timestamp, simultaneous in group.groupby("order_time", sort=True):
            if pd.isna(timestamp) or timestamp < busy_until:
                continue
            pool = simultaneous[~simultaneous.episode_id.astype(str).isin(used)]
            if pool.empty:
                continue
            row = pool.iloc[0].copy()
            selected.append(row)
            used.add(str(row.episode_id))
            terminal = row.terminal_time
            if pd.isna(terminal):
                terminal = timestamp
            busy_until = max(timestamp, terminal)
    return pd.DataFrame(selected).reset_index(drop=True) if selected else candidates.iloc[:0]


def metrics(frame: pd.DataFrame, days: dict[str, int]) -> dict[str, Any]:
    closed = frame[frame.resolved & frame.net_r_num.notna()].copy()
    nav = peak = 1.0
    drawdown = 0.0
    for value in closed.sort_values(["order_time", "episode_id"]).net_r_num.astype(float):
        nav *= max(EPS, 1.0 + RISK * value)
        peak = max(peak, nav)
        drawdown = max(drawdown, 1.0 - nav / peak)
    calendar = int(sum(days.get(str(p), 0) for p in frame.period.astype(str).unique()))
    return {
        "selected_orders": int(len(frame)),
        "closed_trades": int(len(closed)),
        "calendar_days": calendar,
        "trades_per_day": float(len(closed) / max(calendar, 1)),
        "fill_rate": float(frame.filled.mean()) if len(frame) else None,
        "target_first_rate": float(closed.win.mean()) if len(closed) else None,
        "mean_net_r": float(closed.net_r_num.mean()) if len(closed) else None,
        "median_net_r": float(closed.net_r_num.median()) if len(closed) else None,
        "mean_gross_rr": float(num(closed, "gross_rr", np.nan).mean()) if len(closed) else None,
        "median_hold_minutes": float(num(closed, "holding_minutes", np.nan).median()) if len(closed) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(drawdown),
    }


def grouped(frame: pd.DataFrame, key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if key not in frame:
        return rows
    closed = frame[frame.resolved & frame.net_r_num.notna()]
    for value, group in closed.groupby(key, dropna=False):
        rows.append(
            {
                key: str(value),
                "trades": int(len(group)),
                "target_first_rate": float(group.win.mean()),
                "mean_net_r": float(group.net_r_num.mean()),
                "mean_gross_rr": float(num(group, "gross_rr", np.nan).mean()),
            }
        )
    return rows


def diagnose(frame: pd.DataFrame) -> dict[str, Any]:
    closed = frame[frame.resolved & frame.net_r_num.notna()].copy()
    first = closed[closed.is_first_return]
    expansion = closed[closed.is_accepted & ~closed.is_first_return]
    median_rr = float(num(closed, "gross_rr", np.nan).median()) if len(closed) else None
    target_rate = float(closed.win.mean()) if len(closed) else None
    return {
        "implementation_contract": {
            "order_rows": int(len(frame)),
            "duplicate_episode_plans": int(frame.episode_id.astype(str).duplicated().sum()),
            "gross_rr_below_one": int((num(frame, "gross_rr") < 1.0 - 1e-12).sum()),
            "synthetic_entry_fallbacks": int(
                text(frame, "entry_geometry").eq("CAUSAL_DEPARTURE_BAND").sum()
            ),
        },
        "logic_observations": {
            "all_closed_target_rate": target_rate,
            "median_gross_rr": median_rr,
            "first_return_trades": int(len(first)),
            "first_return_target_rate": float(first.win.mean()) if len(first) else None,
            "first_return_mean_r": float(first.net_r_num.mean()) if len(first) else None,
            "accepted_non_first_trades": int(len(expansion)),
            "accepted_non_first_target_rate": float(expansion.win.mean()) if len(expansion) else None,
            "accepted_non_first_mean_r": float(expansion.net_r_num.mean()) if len(expansion) else None,
            "remote_target_mismatch": bool(
                len(closed) >= 10
                and median_rr is not None
                and target_rate is not None
                and median_rr > 3.0
                and target_rate < 0.45
            ),
            "first_return_is_stronger": bool(
                len(first) >= 5
                and len(closed) >= 10
                and float(first.net_r_num.mean()) > float(closed.net_r_num.mean()) + 0.15
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    actions, days = load(args.root)
    actions = enrich(actions)
    masks = policy_masks(actions)

    result: dict[str, Any] = {
        "purpose": "short causal logic diagnosis before any long expansion",
        "risk_fraction": RISK,
        "diagnosis": diagnose(actions),
        "raw_by_period": grouped(actions, "period"),
        "raw_by_family": grouped(actions, "family"),
        "raw_by_geometry": grouped(actions, "entry_geometry"),
        "raw_by_rr_band": grouped(actions, "rr_band"),
        "policies": {},
    }
    all_selected: list[pd.DataFrame] = []
    for name, mask in masks.items():
        selected = route(actions, mask)
        selected["diagnostic_policy"] = name
        all_selected.append(selected)
        result["policies"][name] = {
            "all": metrics(selected, days),
            "dev": metrics(selected[selected.role.eq("dev")], days),
            "fresh": metrics(selected[selected.role.eq("fresh")], days),
            "by_period": grouped(selected, "period"),
            "by_family": grouped(selected, "family"),
            "by_symbol": grouped(selected, "symbol"),
        }

    actions.to_csv(args.output / "enriched_actions.csv.gz", index=False, compression="gzip")
    pd.concat(all_selected, ignore_index=True, sort=False).to_csv(
        args.output / "policy_selected_orders.csv.gz", index=False, compression="gzip"
    )
    losses = actions[actions.resolved & actions.net_r_num.lt(0)].sort_values(
        ["period", "logic_coherence", "target_reachability"]
    )
    losses.head(300).to_csv(args.output / "loss_rows.csv", index=False)
    (args.output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
