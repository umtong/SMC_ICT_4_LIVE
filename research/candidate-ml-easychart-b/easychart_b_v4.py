#!/usr/bin/env python3
"""EasyChart-B V4 fixed causal trigger and structural-target account router.

The target is supplied by ``harvest_structural_v4.py`` and is always a market
structure already visible at arm time. This module does not cap or manufacture
a target from an R number. R appears only as an admissibility/economics measure.

The trigger is a fixed two-head regularized model:
* base-excursion head: probability the event can reach at least +1R before stop;
* completion head: probability the declared natural structure is completed.

Both heads use only order-time causal state. Selection takes the earliest arm
which has revealed control while leaving most of the route unconsumed.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RISK_FRACTION = 0.03
PREFERRED_GEOMETRY = "ZONE_PROXIMAL_LIMIT"

FEATURE_NAMES = [
    "gross_rr_log",
    "target_net_log",
    "target_distance_bps_log",
    "risk_bps_log",
    "target_is_micro_frontier",
    "target_is_impulse_reclaim",
    "target_is_opposing_frontier",
    "family_failed",
    "family_continuation",
    "phase_accepted",
    "phase_first_retest",
    "phase_early",
    "progress_r",
    "consumed_fraction",
    "remaining_fraction",
    "headroom_r",
    "outside_close_ratio",
    "outside_volume_share",
    "acceptance_min",
    "path_efficiency",
    "current_retrace_fraction",
    "activity_log",
    "futures_index_residual",
    "departure_residual_3m",
    "index_return",
    "source_defenses_log",
    "basis_compression",
    "departure_impact_log",
    "approach_delta_toward",
    "pre_departure_return_bps",
    "initial_displacement_bps",
    "late_opposite_delta_share",
    "zone_width_bps_log",
    "acceptance_efficiency",
    "revealed_remaining",
    "controlled_retrace",
    "local_control",
    "route_quality",
]

DECISION_INPUT_COLUMNS = {
    "gross_rr",
    "planned_target_net_r",
    "entry",
    "target",
    "risk_bps",
    "structural_target_provenance",
    "family",
    "auction_phase",
    "arm_progress_r",
    "arm_structural_target_consumed_fraction",
    "arm_structural_target_headroom_r",
    "arm_outside_close_ratio",
    "arm_outside_volume_share",
    "arm_path_efficiency",
    "arm_current_retrace_fraction",
    "arm_activity_ratio",
    "arm_futures_index_residual_signed",
    "departure_residual_return_3m_signed",
    "arm_index_return_signed",
    "source_defense_count",
    "departure_basis_change_3m_signed",
    "departure_impact_per_activity",
    "approach_delta_share_12m_toward",
    "sequence_block_2_return_bps_signed",
    "sequence_block_0_return_bps_signed",
    "sequence_block_5_delta_share_signed",
    "zone_width_bps",
    "entry_geometry",
    "state_id",
    "episode_id",
    "order_time_ns",
    "action_id",
}


def numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    if column not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def text(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    if column not in frame:
        return pd.Series(default, index=frame.index, dtype=str)
    return frame[column].fillna(default).astype(str)


def _safe_log1p(series: pd.Series) -> pd.Series:
    return np.log1p(pd.to_numeric(series, errors="coerce").clip(lower=0.0))


def raw_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Build stable causal features without reading outcome or calendar identity."""
    out = pd.DataFrame(index=frame.index)
    gross = numeric(frame, "gross_rr")
    target_net = numeric(frame, "planned_target_net_r")
    entry = numeric(frame, "entry")
    target = numeric(frame, "target")
    risk_bps = numeric(frame, "risk_bps")

    distance_bps = (target - entry).abs() / entry.abs().clip(lower=1e-12) * 10_000.0
    provenance = text(frame, "structural_target_provenance")
    family = text(frame, "family")
    phase = text(frame, "auction_phase")

    progress = numeric(frame, "arm_progress_r")
    consumed = numeric(frame, "arm_structural_target_consumed_fraction")
    consumed = consumed.where(consumed.notna(), progress / gross.replace(0.0, np.nan))
    remaining = 1.0 - consumed
    headroom = numeric(frame, "arm_structural_target_headroom_r")
    headroom = headroom.where(headroom.notna(), gross - progress)

    outside_close = numeric(frame, "arm_outside_close_ratio")
    outside_volume = numeric(frame, "arm_outside_volume_share")
    acceptance_min = pd.concat([outside_close, outside_volume], axis=1).min(axis=1)
    path = numeric(frame, "arm_path_efficiency")
    retrace = numeric(frame, "arm_current_retrace_fraction")
    activity = numeric(frame, "arm_activity_ratio")
    futures_residual = numeric(frame, "arm_futures_index_residual_signed")
    departure_residual = numeric(frame, "departure_residual_return_3m_signed")
    index_return = numeric(frame, "arm_index_return_signed")
    defenses = numeric(frame, "source_defense_count")
    basis = numeric(frame, "departure_basis_change_3m_signed")
    impact = numeric(frame, "departure_impact_per_activity")
    approach_delta = numeric(frame, "approach_delta_share_12m_toward")
    pre_departure = numeric(frame, "sequence_block_2_return_bps_signed")
    initial_displacement = numeric(frame, "sequence_block_0_return_bps_signed")
    late_delta = numeric(frame, "sequence_block_5_delta_share_signed")
    zone_width = numeric(frame, "zone_width_bps")

    out["gross_rr_log"] = _safe_log1p(gross)
    out["target_net_log"] = _safe_log1p(target_net)
    out["target_distance_bps_log"] = _safe_log1p(distance_bps)
    out["risk_bps_log"] = _safe_log1p(risk_bps)
    out["target_is_micro_frontier"] = provenance.str.contains(
        "PIVOT|RANGE|SESSION|MICRO", regex=True
    ).astype(float)
    out["target_is_impulse_reclaim"] = provenance.eq("IMPULSE_RECLAIM").astype(float)
    out["target_is_opposing_frontier"] = provenance.eq(
        "OPPOSING_LIVE_FRONTIER"
    ).astype(float)
    out["family_failed"] = family.eq("FAILED_AUCTION_REVERSAL").astype(float)
    out["family_continuation"] = family.eq(
        "ACCEPTED_AUCTION_CONTINUATION"
    ).astype(float)
    out["phase_accepted"] = phase.eq("ACCEPTED_EXPANSION").astype(float)
    out["phase_first_retest"] = phase.eq("FIRST_RETEST_FORMING").astype(float)
    out["phase_early"] = phase.eq("EARLY_RESPONSE").astype(float)
    out["progress_r"] = progress
    out["consumed_fraction"] = consumed
    out["remaining_fraction"] = remaining
    out["headroom_r"] = headroom
    out["outside_close_ratio"] = outside_close
    out["outside_volume_share"] = outside_volume
    out["acceptance_min"] = acceptance_min
    out["path_efficiency"] = path
    out["current_retrace_fraction"] = retrace
    out["activity_log"] = np.log1p(activity.clip(lower=0.0))
    out["futures_index_residual"] = futures_residual
    out["departure_residual_3m"] = departure_residual
    out["index_return"] = index_return
    out["source_defenses_log"] = np.log1p(defenses.clip(lower=0.0))
    out["basis_compression"] = -basis
    out["departure_impact_log"] = np.log1p(impact.clip(lower=0.0))
    out["approach_delta_toward"] = approach_delta
    out["pre_departure_return_bps"] = pre_departure
    out["initial_displacement_bps"] = initial_displacement
    out["late_opposite_delta_share"] = -late_delta
    out["zone_width_bps_log"] = _safe_log1p(zone_width)

    acceptance_efficiency = acceptance_min.clip(lower=0.0) * np.sqrt(
        path.clip(lower=0.0)
    )
    local_control = pd.concat(
        [futures_residual, departure_residual], axis=1
    ).max(axis=1)
    out["acceptance_efficiency"] = acceptance_efficiency
    out["revealed_remaining"] = progress.clip(lower=0.0) * remaining.clip(lower=0.0)
    out["controlled_retrace"] = acceptance_efficiency * (
        1.0 - retrace.clip(lower=0.0, upper=1.5)
    )
    out["local_control"] = local_control
    out["route_quality"] = (
        acceptance_efficiency
        * headroom.clip(lower=0.0)
        / (1.0 + target_net.clip(lower=0.0))
    )
    return out.reindex(columns=FEATURE_NAMES)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-values))


def score_actions(frame: pd.DataFrame, policy: dict[str, Any]) -> pd.DataFrame:
    """Score all arm states with the frozen development-only model."""
    model = policy["model"]
    features = raw_feature_frame(frame)
    names = list(model["feature_names"])
    features = features.reindex(columns=names)
    median = np.asarray(model["median"], dtype=float)
    scale = np.asarray(model["scale"], dtype=float)
    matrix = features.to_numpy(float)
    matrix = np.where(np.isfinite(matrix), matrix, median)
    matrix = np.clip((matrix - median) / scale, -8.0, 8.0)
    design = np.column_stack([np.ones(len(matrix), dtype=float), matrix])
    base_probability = _sigmoid(
        design @ np.asarray(model["coef_base_excursion"], dtype=float)
    )
    completion_probability = _sigmoid(
        design @ np.asarray(model["coef_structural_completion"], dtype=float)
    )
    base_weight = float(model.get("base_weight", 0.55))
    eps = 1e-9
    blended_logit = (
        base_weight
        * np.log(
            np.clip(base_probability, eps, 1.0 - eps)
            / np.clip(1.0 - base_probability, eps, 1.0)
        )
        + (1.0 - base_weight)
        * np.log(
            np.clip(completion_probability, eps, 1.0 - eps)
            / np.clip(1.0 - completion_probability, eps, 1.0)
        )
    )
    out = frame.copy()
    out["base_excursion_probability"] = base_probability
    out["structural_completion_probability"] = completion_probability
    out["trigger_score"] = _sigmoid(blended_logit)
    return out


def eligible_mask(frame: pd.DataFrame, selection: dict[str, Any]) -> pd.Series:
    progress = numeric(frame, "arm_progress_r")
    consumed = numeric(frame, "arm_structural_target_consumed_fraction")
    gross = numeric(frame, "gross_rr")
    consumed = consumed.where(consumed.notna(), progress / gross.replace(0.0, np.nan))
    headroom = numeric(frame, "arm_structural_target_headroom_r")
    headroom = headroom.where(headroom.notna(), gross - progress)
    acceptance = pd.concat(
        [
            numeric(frame, "arm_outside_close_ratio"),
            numeric(frame, "arm_outside_volume_share"),
        ],
        axis=1,
    ).min(axis=1)
    phase = text(frame, "auction_phase")
    return (
        numeric(frame, "gross_rr").ge(1.0)
        & numeric(frame, "planned_target_net_r").ge(
            float(selection.get("minimum_net_completion_r", 0.25))
        )
        & progress.ge(float(selection["minimum_progress_r"]))
        & consumed.ge(float(selection.get("minimum_consumed_fraction", 0.0)))
        & consumed.le(float(selection["maximum_consumed_fraction"]))
        & headroom.ge(float(selection.get("minimum_headroom_r", 0.35)))
        & numeric(frame, "arm_current_retrace_fraction").le(
            float(selection["maximum_current_retrace_fraction"])
        )
        & acceptance.ge(float(selection["minimum_acceptance_ratio"]))
        & numeric(frame, "arm_path_efficiency").ge(
            float(selection.get("minimum_path_efficiency", 0.0))
        )
        & numeric(frame, "trigger_score").ge(float(selection["score_threshold"]))
        & phase.isin(
            selection.get(
                "allowed_phases",
                ["EARLY_RESPONSE", "ACCEPTED_EXPANSION", "FIRST_RETEST_FORMING"],
            )
        )
    )


def select_plans(
    frame: pd.DataFrame,
    policy: dict[str, Any],
    *,
    pre_scored: bool = False,
) -> pd.DataFrame:
    scored = frame.copy() if pre_scored else score_actions(frame, policy)
    selected = scored.loc[eligible_mask(scored, policy["selection"])].copy()
    if selected.empty:
        return selected

    selected["preferred_geometry"] = text(
        selected, "entry_geometry"
    ).eq(PREFERRED_GEOMETRY).astype(int)
    selected["target_distance_bps"] = (
        (numeric(selected, "target") - numeric(selected, "entry")).abs()
        / numeric(selected, "entry").abs().clip(lower=1e-12)
        * 10_000.0
    )
    selected["scenario_priority"] = np.where(
        text(selected, "family").eq("FAILED_AUCTION_REVERSAL"), 2, 1
    )

    selected = (
        selected.sort_values(
            [
                "state_id",
                "preferred_geometry",
                "target_distance_bps",
                "trigger_score",
                "action_id",
            ],
            ascending=[True, False, True, False, True],
            kind="mergesort",
        )
        .drop_duplicates("state_id")
        .sort_values(
            [
                "research_period",
                "episode_id",
                "order_time_ns",
                "scenario_priority",
                "trigger_score",
                "action_id",
            ],
            ascending=[True, True, True, False, False, True],
            kind="mergesort",
        )
        .drop_duplicates(["research_period", "episode_id"])
        .sort_values(
            ["order_time_ns", "scenario_priority", "trigger_score", "action_id"],
            ascending=[True, False, False, True],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )
    return selected


def route_continuous_account(
    plans: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    busy_until = -1
    nav = peak = 1.0
    orders: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    for record in plans.sort_values(
        ["order_time_ns", "scenario_priority", "trigger_score", "action_id"],
        ascending=[True, False, False, True],
        kind="mergesort",
    ).to_dict("records"):
        order_time = int(record["order_time_ns"])
        if order_time < busy_until:
            continue
        outcome = str(record.get("outcome", "UNFILLED"))
        if outcome == "UNFILLED":
            terminal = record.get("order_terminal_time_ns")
            if pd.isna(terminal):
                continue
            busy_until = int(terminal)
            record.update(
                account_busy_until_ns=busy_until,
                net_r_num=0.0,
                nav_before=nav,
                nav_after=nav,
                drawdown=1.0 - nav / peak,
            )
            orders.append(record)
            continue
        resolution = record.get("resolution_time_ns")
        net_r = pd.to_numeric(
            pd.Series([record.get("net_r")]), errors="coerce"
        ).iloc[0]
        if pd.isna(resolution) or pd.isna(net_r):
            continue
        busy_until = int(resolution)
        before = nav
        nav = max(0.0, nav * (1.0 + RISK_FRACTION * float(net_r)))
        peak = max(peak, nav)
        record.update(
            account_busy_until_ns=busy_until,
            net_r_num=float(net_r),
            nav_before=before,
            nav_after=nav,
            drawdown=1.0 - nav / peak,
        )
        orders.append(record.copy())
        trades.append(record)
    return pd.DataFrame(orders), pd.DataFrame(trades)


def metric_block(orders: pd.DataFrame, trades: pd.DataFrame) -> dict[str, Any]:
    values = (
        pd.to_numeric(trades.get("net_r_num"), errors="coerce").dropna()
        if len(trades)
        else pd.Series(dtype=float)
    )
    wins = values[values > 0.0]
    losses = values[values < 0.0]
    nav = peak = 1.0
    max_drawdown = 0.0
    for value in values:
        nav = max(0.0, nav * (1.0 + RISK_FRACTION * float(value)))
        peak = max(peak, nav)
        max_drawdown = max(max_drawdown, 1.0 - nav / peak)
    outcomes = text(orders, "outcome") if len(orders) else pd.Series(dtype=str)
    mfe = numeric(trades, "mfe_r") if len(trades) else pd.Series(dtype=float)
    target_net = (
        numeric(trades, "planned_target_net_r")
        if len(trades)
        else pd.Series(dtype=float)
    )
    gross = numeric(trades, "gross_rr") if len(trades) else pd.Series(dtype=float)
    hold = (
        numeric(trades, "holding_minutes")
        if len(trades)
        else pd.Series(dtype=float)
    )
    return {
        "orders": int(len(orders)),
        "unfilled_orders": int(outcomes.eq("UNFILLED").sum()),
        "closed_trades": int(len(values)),
        "wins": int((values > 0.0).sum()),
        "losses": int((values < 0.0).sum()),
        "win_rate": float((values > 0.0).mean()) if len(values) else 0.0,
        "sum_net_r": float(values.sum()) if len(values) else 0.0,
        "mean_net_r": float(values.mean()) if len(values) else 0.0,
        "average_win_r": float(wins.mean()) if len(wins) else 0.0,
        "average_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "payoff_ratio": (
            float(wins.mean() / abs(losses.mean()))
            if len(wins) and len(losses)
            else None
        ),
        "base_excursion_1r_rate": (
            float(mfe.ge(1.0).mean()) if len(mfe.dropna()) else 0.0
        ),
        "base_excursion_1p5r_rate": (
            float(mfe.ge(1.5).mean()) if len(mfe.dropna()) else 0.0
        ),
        "base_excursion_2r_rate": (
            float(mfe.ge(2.0).mean()) if len(mfe.dropna()) else 0.0
        ),
        "mean_planned_gross_rr": float(gross.mean()) if len(gross) else 0.0,
        "median_planned_gross_rr": float(gross.median()) if len(gross) else 0.0,
        "mean_planned_target_net_r": (
            float(target_net.mean()) if len(target_net) else 0.0
        ),
        "median_planned_target_net_r": (
            float(target_net.median()) if len(target_net) else 0.0
        ),
        "median_hold_minutes": float(hold.median()) if len(hold.dropna()) else None,
        "ending_nav": float(nav),
        "max_drawdown": float(max_drawdown),
    }


def _period_from_path(path: Path, period_names: set[str]) -> str:
    joined = "/".join(path.parts)
    matches = [name for name in period_names if name in joined]
    return max(matches, key=len) if matches else path.parent.name


def load_actions(root: Path, period_bounds: dict[str, dict[str, str]]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    names = set(period_bounds)
    for path in sorted(root.rglob("departure_actions.csv.gz")):
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        frame = frame.copy()
        frame["research_period"] = _period_from_path(path, names)
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"No departure_actions.csv.gz below {root}")
    out = pd.concat(frames, ignore_index=True, sort=False)
    out = out.drop_duplicates("action_id", keep="last").reset_index(drop=True)
    out["order_time_ns"] = pd.to_numeric(out["order_time_ns"], errors="coerce")
    out = out[out.order_time_ns.notna()].copy()
    keep = pd.Series(False, index=out.index)
    for period, window in period_bounds.items():
        start = pd.Timestamp(window["start"], tz="UTC").value
        end = pd.Timestamp(window["end"], tz="UTC").value
        keep |= (
            text(out, "research_period").eq(period)
            & numeric(out, "order_time_ns").ge(start)
            & numeric(out, "order_time_ns").lt(end)
        )
    return out.loc[keep].reset_index(drop=True)


def build_summary(
    source: pd.DataFrame,
    selected: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
    period_bounds: dict[str, dict[str, str]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    periods = sorted(period_bounds, key=lambda name: period_bounds[name]["start"])
    calendar_days = sum(
        (date.fromisoformat(w["end"]) - date.fromisoformat(w["start"])).days
        for w in period_bounds.values()
    )
    overall = metric_block(orders, trades)
    overall["calendar_days"] = int(calendar_days)
    overall["closed_trades_per_calendar_day"] = (
        overall["closed_trades"] / max(calendar_days, 1)
    )

    def subset(column: str, value: str) -> dict[str, Any]:
        oo = orders[text(orders, column).eq(value)] if len(orders) else orders
        tt = trades[text(trades, column).eq(value)] if len(trades) else trades
        return metric_block(oo, tt)

    return {
        "policy": "ML_EASYCHART_B_V4_EARLIEST_CAUSAL_CONTROL_STRUCTURAL_TARGET",
        "decision_uses_symbol_identity": False,
        "decision_uses_calendar_fields": False,
        "decision_uses_outcome_fields": False,
        "fixed_r_target_cap": False,
        "target_contract": (
            "nearest still-live causal pivot/range/impulse/opposing liquidity "
            "structure; R is only an admissibility and economics measure"
        ),
        "trigger_contract": (
            "earliest arm where base-excursion and declared-structure completion "
            "probabilities jointly confirm control while most route remains"
        ),
        "decision_input_columns": sorted(DECISION_INPUT_COLUMNS),
        "model_feature_names": FEATURE_NAMES,
        "selection": policy["selection"],
        "eligible_state_plans": int(len(selected)),
        "eligible_episodes": int(selected.episode_id.nunique()) if len(selected) else 0,
        "overall_continuous_account": overall,
        "by_period": {
            period: metric_block(
                orders[text(orders, "research_period").eq(period)]
                if len(orders)
                else orders,
                trades[text(trades, "research_period").eq(period)]
                if len(trades)
                else trades,
            )
            for period in periods
        },
        "by_target_provenance": {
            name: subset("structural_target_provenance", name)
            for name in sorted(
                value
                for value in text(selected, "structural_target_provenance").unique()
                if value
            )
        },
        "by_family": {
            name: subset("family", name)
            for name in (
                "FAILED_AUCTION_REVERSAL",
                "ACCEPTED_AUCTION_CONTINUATION",
            )
        },
        "by_symbol": {
            symbol: subset("symbol", symbol)
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
        },
    }


def run(
    root: Path,
    period_bounds_path: Path,
    policy_path: Path,
    output: Path,
) -> dict[str, Any]:
    bounds = json.loads(period_bounds_path.read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    actions = load_actions(root, bounds)
    scored = score_actions(actions, policy)
    selected = select_plans(scored, policy, pre_scored=True)
    orders, trades = route_continuous_account(selected)
    summary = build_summary(actions, selected, orders, trades, bounds, policy)
    output.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output / "eligible_plans.csv", index=False)
    orders.to_csv(output / "selected_orders.csv", index=False)
    trades.to_csv(output / "closed_trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--period-bounds", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.period_bounds, args.policy, args.output)


if __name__ == "__main__":
    main()
