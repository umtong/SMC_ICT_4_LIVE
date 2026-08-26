#!/usr/bin/env python3
"""Diagnose every selected trade by causal role rather than aggregate score."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
import json
import math

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import RobustScaler

import train_select as selection


def _aligned(row: pd.Series) -> bool | None:
    label = str(row.get("destination_label", ""))
    side = str(row.get("side", ""))
    if label not in {"UPPER_FIRST", "LOWER_FIRST"}:
        return None
    return (side == "LONG" and label == "UPPER_FIRST") or (side == "SHORT" and label == "LOWER_FIRST")


def _diagnosis(row: pd.Series) -> str:
    outcome = str(row.outcome)
    if outcome == "TARGET_FIRST":
        return "COMPLETE_NARRATIVE_WIN"
    aligned = _aligned(row)
    if aligned is False:
        return "DIRECTION_OR_LIQUIDITY_DESTINATION_WRONG"
    if aligned is True and outcome in {"STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"}:
        return "DIRECTION_RIGHT_ENTRY_OR_INVALIDATION_WRONG"
    if outcome == "TIME_EXIT":
        return "THESIS_STALE_WITHOUT_BARRIER_RESOLUTION"
    if aligned is None:
        return "DESTINATION_UNRESOLVED_OR_ROUTE_MAP_INCOMPLETE"
    return "OTHER"


def _group_table(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for keys, group in frame.groupby(columns, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        record = {column: value for column, value in zip(columns, keys)}
        record.update(
            {
                "trades": int(len(group)),
                "wins": int(group.outcome.astype(str).eq("TARGET_FIRST").sum()),
                "win_rate": float(group.outcome.astype(str).eq("TARGET_FIRST").mean()),
                "mean_net_r": float(pd.to_numeric(group.net_r, errors="coerce").mean()),
                "direction_aligned_rate": float(pd.Series([_aligned(row) for _, row in group.iterrows()]).dropna().mean()) if any(_aligned(row) is not None for _, row in group.iterrows()) else np.nan,
                "periods": int(group.period.nunique()),
            }
        )
        rows.append(record)
    return pd.DataFrame(rows).sort_values(["periods", "trades", "mean_net_r"], ascending=[False, False, False])


def _loss_clusters(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    losses = frame[~frame.outcome.astype(str).eq("TARGET_FIRST")].copy()
    numeric_candidates = [
        "liquidity_attraction_normalized",
        "dealing_range_position",
        "structure_multiscale_trend_vote",
        "structure_multiscale_trend_agreement",
        "approach_path_efficiency",
        "approach_delta_share_12m_toward",
        "event_delta_share_signed",
        "event_activity_ratio",
        "confirmation_delta_share_signed",
        "decision_delta_share_signed",
        "response_delta_signed",
        "response_activity_ratio",
        "event_penetration_bps",
        "event_to_confirmation_minutes",
        "return_wait_minutes",
        "response_delay_minutes",
        "risk_bps",
        "gross_rr",
        "destination_probability",
        "action_probability",
    ]
    columns = [column for column in numeric_candidates if column in losses.columns]
    if len(losses) < 8 or len(columns) < 3:
        return losses, pd.DataFrame()
    matrix = losses[columns].apply(pd.to_numeric, errors="coerce")
    imputer = SimpleImputer(strategy="median")
    scaler = RobustScaler(quantile_range=(10, 90))
    x = scaler.fit_transform(imputer.fit_transform(matrix))
    clusters = min(6, max(2, int(round(math.sqrt(len(losses) / 2.0)))))
    model = KMeans(n_clusters=clusters, random_state=1147, n_init=30)
    losses["loss_cluster"] = model.fit_predict(x)
    summaries: list[dict[str, Any]] = []
    for cluster, group in losses.groupby("loss_cluster"):
        record: dict[str, Any] = {
            "loss_cluster": int(cluster),
            "trades": int(len(group)),
            "mean_net_r": float(pd.to_numeric(group.net_r, errors="coerce").mean()),
            "dominant_diagnosis": str(group.diagnosis.value_counts().index[0]),
            "dominant_branch": str(group.narrative_branch.value_counts().index[0]),
            "dominant_setup": str(group.setup_kind.value_counts().index[0]),
            "dominant_location": str(group.location_kind.value_counts().index[0]),
            "dominant_response": str(group.response_kind.value_counts().index[0]),
        }
        for column in columns:
            record[f"median_{column}"] = float(pd.to_numeric(group[column], errors="coerce").median())
        summaries.append(record)
    return losses, pd.DataFrame(summaries).sort_values("trades", ascending=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    actions, states = selection._read_universes(args.root)
    scored = pd.read_csv(args.result / "scored_action_universe.csv")
    destination = states[["period", "state_id", "destination_label"]].drop_duplicates(["period", "state_id"])
    scored = scored.merge(destination, on=["period", "state_id"], how="left")

    selected_frames: list[pd.DataFrame] = []
    for filename in ("development_oof_account_trades.csv", "evaluation_account_trades.csv"):
        path = args.result / filename
        if path.exists():
            frame = pd.read_csv(path)
            if not frame.empty:
                selected_frames.append(frame)
    selected = pd.concat(selected_frames, ignore_index=True, sort=False) if selected_frames else pd.DataFrame()
    if not selected.empty:
        selected = selected.drop(columns=["destination_label"], errors="ignore").merge(
            destination, on=["period", "state_id"], how="left"
        )
        selected["destination_aligned_actual"] = [
            _aligned(row) for _, row in selected.iterrows()
        ]
        selected["diagnosis"] = [_diagnosis(row) for _, row in selected.iterrows()]
        selected.to_csv(args.output / "selected_trade_audit.csv", index=False)

    resolved = scored[scored.outcome.astype(str).isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "TIME_EXIT"])].copy()
    resolved["diagnosis"] = [_diagnosis(row) for _, row in resolved.iterrows()]
    resolved["source_scale_bucket"] = pd.cut(
        pd.to_numeric(resolved.source_timeframe_minutes, errors="coerce"),
        bins=[0, 15, 60, 240, 1440, np.inf],
        labels=["5_15", "15_60", "60_240", "240_1440", "1440_PLUS"],
        include_lowest=True,
    ).astype(str)
    resolved["planned_rr_bucket"] = pd.cut(
        pd.to_numeric(resolved.gross_rr, errors="coerce"),
        bins=[1.0, 1.25, 1.5, 2.0, 3.0, np.inf],
        labels=["1_1.25", "1.25_1.5", "1.5_2", "2_3", "3_PLUS"],
        include_lowest=True,
    ).astype(str)
    resolved.to_csv(args.output / "resolved_action_diagnoses.csv", index=False)

    _group_table(
        resolved,
        ["narrative_branch", "setup_kind", "location_kind", "response_kind"],
    ).to_csv(args.output / "mechanism_diagnosis.csv", index=False)
    _group_table(
        resolved,
        ["source_scale_bucket", "planned_rr_bucket", "diagnosis"],
    ).to_csv(args.output / "geometry_diagnosis.csv", index=False)
    _group_table(
        resolved,
        ["period", "narrative_branch", "diagnosis"],
    ).to_csv(args.output / "period_diagnosis.csv", index=False)

    loss_rows, loss_summary = _loss_clusters(resolved)
    loss_rows.to_csv(args.output / "loss_cluster_members.csv", index=False)
    loss_summary.to_csv(args.output / "loss_clusters.csv", index=False)

    episode = resolved.groupby(["period", "episode_id"]).agg(
        actions=("action_id", "size"),
        any_target_first=("outcome", lambda values: any(str(value) == "TARGET_FIRST" for value in values)),
        best_net_r=("net_r", "max"),
    ).reset_index()
    episode.to_csv(args.output / "episode_opportunity_ceiling.csv", index=False)

    selected_summary = (
        selected.diagnosis.value_counts().to_dict() if not selected.empty else {}
    )
    summary = {
        "resolved_actions": int(len(resolved)),
        "episodes": int(len(episode)),
        "episodes_with_a_winning_action": int(episode.any_target_first.sum()) if not episode.empty else 0,
        "episode_winner_availability": float(episode.any_target_first.mean()) if not episode.empty else None,
        "selected_trades": int(len(selected)),
        "selected_diagnoses": selected_summary,
        "loss_clusters": int(len(loss_summary)),
    }
    (args.output / "audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
