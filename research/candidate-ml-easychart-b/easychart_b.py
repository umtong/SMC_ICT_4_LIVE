#!/usr/bin/env python3
"""EasyChart-B causal liquidity-control router.

The policy is one decision process, not five indicator strategies.  It asks whether an
important liquidity interaction has transferred executable control before the first
credible opposing auction frontier:

1. failed-auction relative control: the broad market remains adverse while the traded
   instrument already recovers into accepted expansion;
2. defended-source absorption: basis compresses with very low price impact at a source
   repeatedly defended by the market;
3. push-pull first return: strong displacement is met by late opposite order-flow at a
   meaningful footprint, then the first accepted return is traded;
4. passive defended residual control: low-aggression approach plus repeated source
   defense leaves positive residual control.

OB/FVG/BPR and channel/boundary vocabulary locate the source/entry and destination. They
never create an independent trade.  All decisions use fields observable at order time;
future outcome fields are used only by the inherited immutable labeler.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RISK_FRACTION = 0.03
MIN_TARGET_NET_R = 1.0
MAX_TARGET_NET_R = 1.500001
PREFERRED_GEOMETRY = "ZONE_PROXIMAL_LIMIT"

# Stable causal scales inherited from the auction/episode line.  They describe market
# mechanisms and are shared by all four symbols; no symbol identity enters selection.
BASIS_COMPRESSION_BPS = -1.351867
LOW_DEPARTURE_IMPACT = 0.11387988
MIN_SOURCE_DEFENSES = 5.0
DEEP_RETEST_MIN_DEFENSES = 7.0

BROAD_ADVERSE_RETURN_30M = 0.0
LOCAL_RESIDUAL_RETURN_3M = 0.0

INITIAL_DISPLACEMENT_BPS = 35.48466
LATE_OPPOSITE_DELTA_SHARE = -0.13257523
MIN_MEANINGFUL_ZONE_BPS = 7.877117

PASSIVE_APPROACH_DELTA_SHARE = 0.08
PASSIVE_DEFENDED_SOURCE_DEFENSES = 7.0
MIN_RESIDUAL_CONTROL_5M = 0.001

SCENARIO_PRIORITY = {
    "DEFENDED_SOURCE_ABSORPTION": 4,
    "RELATIVE_CONTROL_TRANSFER": 3,
    "PUSH_PULL_FIRST_RETURN": 2,
    "PASSIVE_DEFENDED_RESIDUAL_CONTROL": 1,
}

DECISION_COLUMNS = {
    "family",
    "auction_phase",
    "location_kind",
    "target_scale_minutes",
    "departure_common_return_30m_signed",
    "departure_residual_return_3m_signed",
    "departure_basis_change_3m_signed",
    "departure_impact_per_activity",
    "source_defense_count",
    "sequence_block_0_return_bps_signed",
    "sequence_block_5_delta_share_signed",
    "zone_width_bps",
    "approach_delta_share_12m_toward",
    "departure_residual_return_5m_signed",
    "planned_target_net_r",
    "entry_geometry",
    "state_id",
    "episode_id",
    "order_time_ns",
    "action_id",
}


def numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def text(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series("", index=frame.index, dtype=str)
    return frame[column].fillna("").astype(str)


def scenario_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    family = text(frame, "family")
    phase = text(frame, "auction_phase")
    location = text(frame, "location_kind")
    target_scale = numeric(frame, "target_scale_minutes")
    defenses = numeric(frame, "source_defense_count")

    failed = family.eq("FAILED_AUCTION_REVERSAL")
    accepted = family.eq("ACCEPTED_AUCTION_CONTINUATION")
    footprint = location.str.contains("FVG|OB_OVERLAP", regex=True)

    defended_source = (
        numeric(frame, "departure_basis_change_3m_signed").le(BASIS_COMPRESSION_BPS)
        & numeric(frame, "departure_impact_per_activity").le(LOW_DEPARTURE_IMPACT)
        & defenses.ge(MIN_SOURCE_DEFENSES)
        & (~phase.eq("DEEP_RETEST") | defenses.ge(DEEP_RETEST_MIN_DEFENSES))
    )

    relative_control = (
        failed
        & numeric(frame, "departure_common_return_30m_signed").le(
            BROAD_ADVERSE_RETURN_30M
        )
        & numeric(frame, "departure_residual_return_3m_signed").ge(
            LOCAL_RESIDUAL_RETURN_3M
        )
        & phase.eq("ACCEPTED_EXPANSION")
        & (target_scale.eq(1440.0) | location.eq("BPR"))
    )

    push_pull = (
        numeric(frame, "sequence_block_0_return_bps_signed").ge(
            INITIAL_DISPLACEMENT_BPS
        )
        & numeric(frame, "sequence_block_5_delta_share_signed").le(
            LATE_OPPOSITE_DELTA_SHARE
        )
        & numeric(frame, "zone_width_bps").ge(MIN_MEANINGFUL_ZONE_BPS)
        & footprint
        & phase.isin({"ACCEPTED_EXPANSION", "FIRST_RETEST_FORMING"})
        & target_scale.eq(1440.0)
    )

    passive_residual = (
        numeric(frame, "approach_delta_share_12m_toward").le(
            PASSIVE_APPROACH_DELTA_SHARE
        )
        & defenses.ge(PASSIVE_DEFENDED_SOURCE_DEFENSES)
        & numeric(frame, "departure_residual_return_5m_signed").ge(
            MIN_RESIDUAL_CONTROL_5M
        )
        & (
            failed
            | (
                accepted
                & ~location.eq("TRANSFERRED_BOUNDARY")
                & (
                    ~location.eq("BOUNDARY_FVG_OVERLAP")
                    | numeric(frame, "zone_width_bps").ge(6.0039672)
                )
            )
        )
    )

    return {
        "DEFENDED_SOURCE_ABSORPTION": defended_source,
        "RELATIVE_CONTROL_TRANSFER": relative_control,
        "PUSH_PULL_FIRST_RETURN": push_pull,
        "PASSIVE_DEFENDED_RESIDUAL_CONTROL": passive_residual,
    }


def select_plans(frame: pd.DataFrame) -> pd.DataFrame:
    masks = scenario_masks(frame)
    eligible = pd.concat(masks, axis=1).any(axis=1)
    selected = frame.loc[eligible].copy()
    if selected.empty:
        return selected

    selected["scenario_family"] = ""
    # Higher-priority mechanisms overwrite lower-priority labels when one causal state
    # satisfies both descriptions.  This changes attribution, not the order itself.
    for name, priority in sorted(SCENARIO_PRIORITY.items(), key=lambda item: item[1]):
        selected.loc[
            masks[name].reindex(selected.index, fill_value=False), "scenario_family"
        ] = name
    selected["scenario_priority"] = (
        selected["scenario_family"].map(SCENARIO_PRIORITY).astype(int)
    )
    selected = selected[
        numeric(selected, "planned_target_net_r").between(
            MIN_TARGET_NET_R, MAX_TARGET_NET_R
        )
    ].copy()
    selected["preferred_geometry"] = (
        text(selected, "entry_geometry").eq(PREFERRED_GEOMETRY).astype(int)
    )

    # The same causal state can expose alternative entry geometries.  Prefer the source
    # proximal limit, then the nearer already-valid target; never use the outcome.
    selected = (
        selected.sort_values(
            ["state_id", "preferred_geometry", "planned_target_net_r", "action_id"],
            ascending=[True, False, True, True],
            kind="mergesort",
        )
        .drop_duplicates("state_id")
        .sort_values(
            ["order_time_ns", "scenario_priority", "planned_target_net_r", "action_id"],
            ascending=[True, False, True, True],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )
    return selected


def first_plan_per_episode(plans: pd.DataFrame) -> pd.DataFrame:
    if plans.empty:
        return plans.copy()
    order = ["order_time_ns", "scenario_priority", "planned_target_net_r", "action_id"]
    ascending = [True, False, True, True]
    return (
        plans.sort_values(order, ascending=ascending, kind="mergesort")
        .drop_duplicates(["research_period", "episode_id"])
        .sort_values(order, ascending=ascending, kind="mergesort")
        .reset_index(drop=True)
    )


def route_continuous_account(
    plans: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply one global pending/position slot and continuous 3%-risk NAV."""
    busy_until = -1
    nav = peak = 1.0
    orders: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    for record in first_plan_per_episode(plans).to_dict("records"):
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
        if pd.isna(resolution):
            continue
        busy_until = int(resolution)
        net_r = (
            float(record["net_r"])
            if pd.notna(record.get("net_r"))
            else -1.0
        )
        before = nav
        nav = max(0.0, nav * (1.0 + RISK_FRACTION * net_r))
        peak = max(peak, nav)
        record.update(
            account_busy_until_ns=busy_until,
            net_r_num=net_r,
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

    outcomes = (
        text(orders, "outcome") if len(orders) else pd.Series(dtype=str)
    )
    hold_minutes = pd.Series(dtype=float)
    if len(trades):
        hold_minutes = (
            numeric(trades, "resolution_time_ns")
            - numeric(trades, "order_time_ns")
        ) / 60_000_000_000.0
        hold_minutes = hold_minutes[np.isfinite(hold_minutes) & hold_minutes.ge(0.0)]

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
        "mean_planned_target_net_r": (
            float(numeric(trades, "planned_target_net_r").mean())
            if len(trades)
            else 0.0
        ),
        "median_hold_minutes": (
            float(hold_minutes.median()) if len(hold_minutes) else None
        ),
        "mean_hold_minutes": (
            float(hold_minutes.mean()) if len(hold_minutes) else None
        ),
        "ending_nav": float(nav),
        "max_drawdown": float(max_drawdown),
    }


def build_summary(
    source: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
    period_bounds: dict[str, dict[str, str]] | None,
) -> dict[str, Any]:
    periods = sorted(
        source["research_period"].astype(str).unique(),
        key=lambda period: int(
            numeric(
                source[source["research_period"].astype(str).eq(period)],
                "order_time_ns",
            ).min()
        ),
    )
    calendar_days = None
    if period_bounds:
        calendar_days = sum(
            (
                date.fromisoformat(window["end"])
                - date.fromisoformat(window["start"])
            ).days
            for window in period_bounds.values()
        )
    overall = metric_block(orders, trades)
    if calendar_days:
        overall["calendar_days"] = int(calendar_days)
        overall["closed_trades_per_calendar_day"] = (
            overall["closed_trades"] / calendar_days
        )

    return {
        "policy": "ML_EASYCHART_B_CAUSAL_LIQUIDITY_CONTROL_V1",
        "decision_uses_symbol_identity": False,
        "decision_uses_outcome_fields": False,
        "decision_columns": sorted(DECISION_COLUMNS),
        "account": {
            "one_global_pending_or_position_slot": True,
            "one_plan_per_causal_episode": True,
            "risk_fraction_of_current_nav": RISK_FRACTION,
            "full_nav_margin_base": True,
            "scale_in_or_out": False,
            "daily_loss_or_trade_cap": False,
            "entry_stop_target_immutable_before_fill": True,
            "minimum_planned_target_net_r": MIN_TARGET_NET_R,
            "maximum_planned_target_net_r": 1.5,
            "preferred_entry_geometry": PREFERRED_GEOMETRY,
        },
        "overall_continuous_account": overall,
        "by_period": {
            period: metric_block(
                orders[orders["research_period"].astype(str).eq(period)]
                if len(orders)
                else orders,
                trades[trades["research_period"].astype(str).eq(period)]
                if len(trades)
                else trades,
            )
            for period in periods
        },
        "by_scenario_family": {
            name: metric_block(
                orders[orders["scenario_family"].astype(str).eq(name)]
                if len(orders)
                else orders,
                trades[trades["scenario_family"].astype(str).eq(name)]
                if len(trades)
                else trades,
            )
            for name in SCENARIO_PRIORITY
        },
        "by_symbol": {
            symbol: metric_block(
                orders[orders["symbol"].astype(str).eq(symbol)]
                if len(orders)
                else orders,
                trades[trades["symbol"].astype(str).eq(symbol)]
                if len(trades)
                else trades,
            )
            for symbol in sorted(source["symbol"].astype(str).unique())
        },
    }


def load_actions(root: Path) -> pd.DataFrame:
    files = sorted(root.rglob("departure_actions.csv.gz"))
    if not files:
        raise FileNotFoundError(f"no departure_actions.csv.gz below {root}")
    frames: list[pd.DataFrame] = []
    for path in files:
        frame = pd.read_csv(path, low_memory=False)
        period = path.parent.name
        if period.endswith("USDT"):
            period = path.parent.parent.name
        frame["research_period"] = period
        frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False)


def apply_period_bounds(
    frame: pd.DataFrame, path: Path | None
) -> tuple[pd.DataFrame, dict[str, dict[str, str]] | None]:
    if path is None:
        return frame, None
    bounds = json.loads(path.read_text(encoding="utf-8"))
    timestamp = numeric(frame, "order_time_ns")
    keep = pd.Series(False, index=frame.index)
    for period, window in bounds.items():
        keep |= (
            frame["research_period"].astype(str).eq(period)
            & timestamp.ge(pd.Timestamp(window["start"], tz="UTC").value)
            & timestamp.lt(pd.Timestamp(window["end"], tz="UTC").value)
        )
    return frame.loc[keep].copy(), bounds


def run(
    root: Path,
    output: Path,
    period_bounds_path: Path | None = None,
) -> dict[str, Any]:
    source, period_bounds = apply_period_bounds(
        load_actions(root), period_bounds_path
    )
    plans = select_plans(source)
    orders, trades = route_continuous_account(plans)
    summary = build_summary(source, orders, trades, period_bounds)
    output.mkdir(parents=True, exist_ok=True)
    orders.to_csv(output / "selected_orders.csv", index=False)
    trades.to_csv(output / "closed_trades.csv", index=False)
    plans.to_csv(output / "eligible_plans.csv.gz", index=False, compression="gzip")
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--period-bounds", type=Path)
    args = parser.parse_args()
    run(args.root, args.output, args.period_bounds)


if __name__ == "__main__":
    main()
