#!/usr/bin/env python3
"""Compress the short clinic into market-logic diagnostics, not promotion gates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PUBLIC_ETH_ENTRIES = {
    "PUBLIC_ETH_LONG_4226": 4226.40,
    "PUBLIC_ETH_LONG_4264": 4264.00,
    "PUBLIC_ETH_SHORT_4285": 4285.11,
}


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _safe_div(left: pd.Series, right: pd.Series) -> pd.Series:
    denominator = pd.to_numeric(right, errors="coerce").replace(0.0, np.nan)
    return pd.to_numeric(left, errors="coerce") / denominator


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    output = frame.copy()
    for column in (
        "entry", "stop", "target", "gross_rr", "target_net_r", "net_r", "mfe_r", "mae_r",
        "zone_lower", "zone_upper", "event_strength", "event_outside_close_ratio",
        "event_outside_volume_ratio", "event_path_efficiency", "event_activity_ratio",
        "event_flow_signed", "event_volume_clock", "response_strength",
        "response_flow_signed", "response_activity_ratio", "response_body_atr",
        "route_clarity", "route_barrier_count", "route_strongest_barrier_ratio",
        "route_volume_congestion", "structure_multiscale_trend_vote", "arbitration_score",
        "holding_minutes", "source_semantic_weight", "source_timeframe_minutes",
    ):
        if column in output:
            output[column] = pd.to_numeric(output[column], errors="coerce")
    risk = (output.entry - output.stop).abs()
    output["stop_distance_bps"] = _safe_div(risk * 10_000.0, output.entry.abs())
    long = output.side.eq("LONG")
    chase = np.where(
        long,
        np.maximum(output.entry - output.zone_upper, 0.0),
        np.maximum(output.zone_lower - output.entry, 0.0),
    )
    output["zone_chase_bps"] = np.asarray(chase, dtype=float) / output.entry.abs() * 10_000.0
    output["zone_chase_r"] = np.asarray(chase, dtype=float) / risk.replace(0.0, np.nan)
    output["cost_and_execution_haircut_r"] = output.gross_rr - output.target_net_r
    output["target_reach_fraction"] = _safe_div(output.mfe_r, output.target_net_r)
    output["near_target_then_stop"] = output.net_r.lt(0.0) & output.target_reach_fraction.ge(0.85)
    output["immediate_failure"] = output.net_r.lt(0.0) & output.holding_minutes.le(10.0)
    output["response_against_aggregate_flow"] = output.response_flow_signed.lt(0.0)
    sign = np.where(long, 1.0, -1.0)
    output["structure_alignment"] = sign * output.structure_multiscale_trend_vote
    output["entry_time_utc"] = pd.to_datetime(output.entry_time_ns, unit="ns", utc=True)
    output["entry_time_kst"] = output.entry_time_utc.dt.tz_convert("Asia/Seoul")
    output["interaction_time_utc"] = pd.to_datetime(output.interaction_time_ns, unit="ns", utc=True)
    output = output.sort_values(["entry_time_ns", "symbol", "episode_id"]).reset_index(drop=True)

    clusters: list[int] = []
    cluster = 0
    previous: dict[str, tuple[int, float, str]] = {}
    for row in output.itertuples():
        current_ns = int(row.interaction_time_ns)
        current_price = float(row.source_price)
        key = str(row.symbol)
        prior = previous.get(key)
        same_episode = False
        if prior is not None:
            prior_ns, prior_price, prior_side = prior
            elapsed_minutes = (current_ns - prior_ns) / 60e9
            relative_distance = abs(current_price - prior_price) / max(abs(current_price), 1e-12)
            same_episode = elapsed_minutes <= 90.0 and relative_distance <= 0.015 and str(row.source_side) == prior_side
        if not same_episode:
            cluster += 1
        clusters.append(cluster)
        previous[key] = current_ns, current_price, str(row.source_side)
    output["parent_episode_cluster"] = clusters
    return output


def metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"trades": 0}
    values = pd.to_numeric(frame.net_r, errors="coerce").dropna()
    winners = values[values > 0.0]
    losers = values[values < 0.0]
    return {
        "trades": int(len(values)),
        "wins": int((values > 0.0).sum()),
        "win_rate": float((values > 0.0).mean()) if len(values) else 0.0,
        "average_net_r": float(values.mean()) if len(values) else 0.0,
        "profit_factor": float(winners.sum() / abs(losers.sum())) if len(winners) and len(losers) else 0.0,
        "mean_stop_distance_bps": float(frame.stop_distance_bps.mean()),
        "median_stop_distance_bps": float(frame.stop_distance_bps.median()),
        "mean_zone_chase_r": float(frame.zone_chase_r.mean()),
        "chased_over_quarter_r": int(frame.zone_chase_r.ge(0.25).sum()),
        "near_target_then_stop": int(frame.near_target_then_stop.sum()),
        "immediate_failures": int(frame.immediate_failure.sum()),
        "aggregate_adverse_flow_responses": int(frame.response_against_aggregate_flow.sum()),
        "mean_cost_and_execution_haircut_r": float(frame.cost_and_execution_haircut_r.mean()),
        "mean_target_reach_fraction_on_losses": float(frame.loc[frame.net_r < 0.0, "target_reach_fraction"].mean()),
        "parent_episode_clusters": int(frame.parent_episode_cluster.nunique()),
        "trades_minus_parent_clusters": int(len(frame) - frame.parent_episode_cluster.nunique()),
    }


def group_metrics(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty or column not in frame:
        return {}
    return {str(name): metrics(group) for name, group in frame.groupby(column, dropna=False)}


def feature_contrast(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {}
    columns = [
        "event_strength", "event_outside_close_ratio", "event_outside_volume_ratio",
        "event_path_efficiency", "event_activity_ratio", "event_flow_signed",
        "event_volume_clock", "response_strength", "response_flow_signed",
        "response_activity_ratio", "response_body_atr", "route_clarity",
        "route_barrier_count", "route_strongest_barrier_ratio", "route_volume_congestion",
        "structure_alignment", "source_semantic_weight", "source_timeframe_minutes",
        "stop_distance_bps", "zone_chase_r", "cost_and_execution_haircut_r",
    ]
    output: dict[str, Any] = {}
    for family, group in frame.groupby("family"):
        item: dict[str, Any] = {}
        for outcome, subset in (("winner", group[group.net_r > 0.0]), ("loser", group[group.net_r < 0.0])):
            item[outcome] = {
                column: float(pd.to_numeric(subset[column], errors="coerce").mean())
                for column in columns if column in subset and len(subset)
            }
        output[str(family)] = item
    return output


def public_matches(plans: pd.DataFrame) -> pd.DataFrame:
    if plans.empty:
        return pd.DataFrame()
    eth = plans[plans.symbol.eq("ETHUSDT")].copy()
    if eth.empty:
        return eth
    records = []
    for public_id, public_price in PUBLIC_ETH_ENTRIES.items():
        match = eth.copy()
        match["public_id"] = public_id
        match["public_entry"] = public_price
        match["entry_distance_pct"] = (match.entry - public_price).abs() / public_price
        match = match.sort_values(["entry_distance_pct", "entry_time_ns"]).head(8)
        records.append(match)
    output = pd.concat(records, ignore_index=True)
    output["entry_time_utc"] = pd.to_datetime(output.entry_time_ns, unit="ns", utc=True)
    output["entry_time_kst"] = output.entry_time_utc.dt.tz_convert("Asia/Seoul")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    selected = enrich(_read(args.input / "global_trades.csv"))
    skipped = enrich(_read(args.input / "account_skipped.csv"))
    plans_path = args.input / "candidate_plans.csv.gz"
    plans = enrich(pd.read_csv(plans_path)) if plans_path.exists() else pd.DataFrame()

    compact_columns = [
        "entry_time_utc", "entry_time_kst", "episode_id", "parent_episode_cluster",
        "symbol", "side", "family", "source_kind", "source_timeframe_minutes",
        "location_kind", "response_kind", "entry", "stop", "target", "gross_rr",
        "target_net_r", "net_r", "holding_minutes", "mfe_r", "mae_r",
        "stop_distance_bps", "zone_chase_bps", "zone_chase_r",
        "cost_and_execution_haircut_r", "target_reach_fraction", "near_target_then_stop",
        "immediate_failure", "event_outside_close_ratio", "event_outside_volume_ratio",
        "event_flow_signed", "response_flow_signed", "route_clarity",
        "route_barrier_count", "route_strongest_barrier_ratio", "structure_alignment",
        "arbitration_score",
    ]
    selected[[column for column in compact_columns if column in selected]].to_csv(
        args.output / "selected_trade_clinic.csv", index=False
    )
    if not skipped.empty:
        skipped[[column for column in compact_columns if column in skipped]].to_csv(
            args.output / "account_skipped_clinic.csv", index=False
        )
    matches = public_matches(plans)
    match_columns = [
        "public_id", "public_entry", "entry_distance_pct", "entry_time_utc", "entry_time_kst",
        "episode_id", "symbol", "side", "family", "source_kind", "location_kind",
        "entry", "stop", "target", "gross_rr", "target_net_r", "net_r", "mfe_r", "mae_r",
        "zone_chase_r", "event_outside_close_ratio", "event_outside_volume_ratio",
        "event_flow_signed", "response_flow_signed", "route_clarity", "arbitration_score",
    ]
    matches[[column for column in match_columns if column in matches]].to_csv(
        args.output / "public_eth_price_matches.csv", index=False
    )

    result = {
        "selected": metrics(selected),
        "all_candidate_plans": metrics(plans),
        "account_skipped": metrics(skipped),
        "selected_by_symbol": group_metrics(selected, "symbol"),
        "selected_by_family": group_metrics(selected, "family"),
        "selected_by_response_kind": group_metrics(selected, "response_kind"),
        "selected_by_location_kind": group_metrics(selected, "location_kind"),
        "selected_by_source_timeframe": group_metrics(selected, "source_timeframe_minutes"),
        "winner_loser_feature_contrast": feature_contrast(selected),
        "structural_observations": {
            "selected_losses_reaching_at_least_85pct_of_target": int(selected.near_target_then_stop.sum()) if not selected.empty else 0,
            "selected_entries_chasing_more_than_quarter_stop_distance": int(selected.zone_chase_r.ge(0.25).sum()) if not selected.empty else 0,
            "selected_losing_responses_with_aggregate_flow_still_adverse": int((selected.net_r.lt(0.0) & selected.response_against_aggregate_flow).sum()) if not selected.empty else 0,
            "selected_repeated_parent_episode_entries": int(len(selected) - selected.parent_episode_cluster.nunique()) if not selected.empty else 0,
        },
    }
    (args.output / "mechanism_summary.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
