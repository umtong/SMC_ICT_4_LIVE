#!/usr/bin/env python3
"""Route independent causal mechanisms through one continuous account.

This is a trading policy, not a candidate-ranking contest.  Each family first
has to satisfy its own market mechanism.  At a shared timestamp the account
uses a simple lexicographic arbitration; otherwise the first eligible episode
owns the single account slot until its declared TP or SL resolves.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd


RISK_FRACTION = 0.03
NS_PER_MINUTE = 60_000_000_000
PERIOD_PATTERN = re.compile(
    r"(?P<role>dev|fresh|cal|eval|holdout)-(?P<year>\d{4})-(?P<label>[a-z0-9-]+)"
)


def _finite(value: Any, default: float = float("nan")) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _period_name(path: Path) -> str:
    match = PERIOD_PATTERN.search(str(path))
    return match.group(0) if match else path.parent.name


def load_periods(root: Path) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    metadata: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("candidate_actions.csv.gz")):
        period = _period_name(path)
        summary_path = path.parent / "summary.json"
        if not summary_path.exists():
            raise RuntimeError(f"missing summary beside {path}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        start = pd.Timestamp(summary["start"], tz="UTC")
        end = pd.Timestamp(summary["end"], tz="UTC")
        label_end = pd.Timestamp(summary["label_data_end"], tz="UTC")
        metadata[period] = {
            "period": period,
            "start": summary["start"],
            "end": summary["end"],
            "label_data_end": summary["label_data_end"],
            "calendar_days": int((end - start).days),
            "start_ns": int(start.value),
            "end_ns": int(end.value),
            "label_end_ns": int(label_end.value),
        }
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        frame["period"] = period
        frame["period_start_ns"] = int(start.value)
        frame["period_end_ns"] = int(end.value)
        frame["period_label_end_ns"] = int(label_end.value)
        frames.append(frame)
    if not metadata:
        raise RuntimeError(f"no candidate periods below {root}")
    combined = (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )
    return combined, metadata


def structural_eligibility(row: pd.Series) -> tuple[bool, str]:
    """Return the causal mechanism decision using only emission-time fields."""
    target_r = _finite(row.get("account_target_r"))
    risk_bps = _finite(row.get("risk_bps"))
    if not math.isfinite(target_r) or target_r <= 0.0:
        return False, "NON_POSITIVE_COST_INCLUSIVE_OBJECTIVE"
    if not math.isfinite(risk_bps) or risk_bps < 30.0:
        return False, "STRUCTURAL_STOP_TOO_CLOSE_TO_MICRO_NOISE_AND_COST"

    family = str(row.get("scenario_family", ""))
    if family == "LIQUIDITY_DISPLACEMENT":
        penetration = _finite(row.get("event_penetration_bps"))
        penetration_fraction = penetration / max(risk_bps, 1e-12)
        activity = _finite(row.get("displacement_activity_ratio"))
        if str(row.get("response_kind", "")) != "ALIGNED_INITIATIVE":
            return False, "FIRST_RETURN_DID_NOT_REGAIN_ALIGNED_INITIATIVE"
        if not math.isfinite(penetration_fraction) or penetration_fraction > 0.40:
            return False, "LIQUIDITY_PENETRATION_TOO_DEEP_RELATIVE_TO_INVALIDATION"
        if not math.isfinite(activity) or activity < 2.0:
            return False, "DISPLACEMENT_LACKED_MEANINGFUL_PARTICIPATION"
        if activity > 5.0:
            return False, "DISPLACEMENT_WAS_BLOWOFF_ACTIVITY_NOT_CONTROLLED_TRANSFER"
        return True, "ELIGIBLE_LIQUIDITY_DISPLACEMENT"

    if family == "DERIVATIVES_DISLOCATION":
        reward_risk = _finite(row.get("post_cost_reward_risk"))
        if not math.isfinite(reward_risk) or reward_risk < 1.0:
            return False, "DERIVATIVES_OBJECTIVE_PAYS_LESS_THAN_COST_INCLUSIVE_1R"
        mechanism = str(row.get("causal_mechanism", row.get("mechanism", "")))
        if mechanism == "SPOT_CONFIRMED_INITIATIVE":
            if _finite(row.get("common_return_60m_signed"), 0.0) <= 0.0:
                return False, "INITIATIVE_OPPOSES_COMMON_60M_AUCTION"
            if _finite(row.get("common_breadth_60m_signed"), 0.0) < 0.5:
                return False, "INITIATIVE_LACKS_THREE_OF_FOUR_MARKET_BREADTH"
            if _finite(row.get("index_return_15m_signed"), 0.0) <= 0.0:
                return False, "INITIATIVE_LACKS_SPOT_INDEX_15M_CONFIRMATION"
            return True, "ELIGIBLE_SPOT_CONFIRMED_INITIATIVE"
        if mechanism == "FORCED_FLUSH_REVERSAL":
            if _finite(row.get("source_timeframe_minutes"), 0.0) < 60.0:
                return False, "FORCED_FLUSH_DID_NOT_OCCUR_AT_HTF_LIQUIDITY"
            if _finite(row.get("basis_repair_fraction"), 0.0) < 0.5:
                return False, "FORCED_FLUSH_BASIS_DID_NOT_REPAIR"
            if _finite(row.get("common_return_15m_signed"), 0.0) < 0.0:
                return False, "REVERSAL_OPPOSES_CURRENT_COMMON_15M_AUCTION"
            if _finite(row.get("common_breadth_15m_signed"), 0.0) < 0.0:
                return False, "REVERSAL_LACKS_NON_OPPOSING_MARKET_BREADTH"
            if _finite(row.get("index_return_15m_signed"), 0.0) < 0.0:
                return False, "REVERSAL_LACKS_SPOT_INDEX_REPAIR"
            return True, "ELIGIBLE_FORCED_FLUSH_REVERSAL"
        return False, "UNKNOWN_DERIVATIVES_MECHANISM"

    return False, "UNKNOWN_SCENARIO_FAMILY"


def apply_policy(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    output = frame.copy()
    decisions = output.apply(structural_eligibility, axis=1)
    output["eligible"] = [item[0] for item in decisions]
    output["policy_reason"] = [item[1] for item in decisions]
    output["penetration_fraction_of_risk"] = (
        pd.to_numeric(output.get("event_penetration_bps"), errors="coerce")
        / pd.to_numeric(output.get("risk_bps"), errors="coerce").replace(0.0, np.nan)
    )
    output["family_priority"] = np.where(
        output["scenario_family"].astype(str).eq("LIQUIDITY_DISPLACEMENT"),
        2,
        1,
    )
    output["source_priority"] = pd.to_numeric(
        output.get("source_timeframe_minutes"), errors="coerce"
    ).fillna(0.0)
    output["objective_priority"] = pd.to_numeric(
        output.get("account_target_r"), errors="coerce"
    ).fillna(0.0)
    return output


def _terminal_ns(row: pd.Series) -> int:
    resolution = _finite(row.get("resolution_time_ns"))
    if math.isfinite(resolution):
        return int(resolution)
    fill_state = str(row.get("fill_state", ""))
    if not fill_state.startswith("FILLED"):
        expiry = max(1.0, _finite(row.get("entry_expiry_minutes"), 1.0))
        return int(_finite(row.get("emission_time_ns"), 0.0) + expiry * NS_PER_MINUTE)
    # A filled unresolved plan continues to own the account; no time exit is invented.
    return int(_finite(row.get("period_label_end_ns"), row.get("period_end_ns", 0.0)))


def route_one_account(
    scored: pd.DataFrame,
    metadata: dict[str, dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    eligible = scored[scored["eligible"]].copy() if not scored.empty else scored.copy()
    rejected = scored[~scored["eligible"]].copy() if not scored.empty else scored.copy()
    selected_rows: list[pd.Series] = []
    skipped_rows: list[pd.Series] = []
    period_order = sorted(metadata, key=lambda name: metadata[name]["start_ns"])

    for period in period_order:
        group = eligible[eligible["period"] == period].copy()
        if group.empty:
            continue
        group["emission_time_ns"] = pd.to_numeric(
            group["emission_time_ns"], errors="coerce"
        )
        group = group.sort_values(
            [
                "emission_time_ns",
                "family_priority",
                "source_priority",
                "objective_priority",
                "action_id",
            ],
            ascending=[True, False, False, False, True],
        )
        busy_until = -np.inf
        used_episodes: set[str] = set()
        for timestamp, simultaneous in group.groupby("emission_time_ns", sort=True):
            timestamp = float(timestamp)
            if not math.isfinite(timestamp):
                continue
            if timestamp < busy_until:
                for _, row in simultaneous.iterrows():
                    skipped = row.copy()
                    skipped["routing_reason"] = "ACCOUNT_BUSY"
                    skipped_rows.append(skipped)
                continue
            available = simultaneous[
                ~simultaneous["episode_id"].astype(str).isin(used_episodes)
            ]
            if available.empty:
                continue
            row = available.iloc[0].copy()
            row["routing_reason"] = "SELECTED_FIRST_ELIGIBLE_EPISODE"
            selected_rows.append(row)
            used_episodes.add(str(row["episode_id"]))
            busy_until = max(timestamp, float(_terminal_ns(row)))
            for _, other in simultaneous.iloc[1:].iterrows():
                skipped = other.copy()
                skipped["routing_reason"] = "SIMULTANEOUS_LOWER_PRIORITY"
                skipped_rows.append(skipped)

    selected = (
        pd.DataFrame(selected_rows).reset_index(drop=True)
        if selected_rows
        else eligible.iloc[:0].copy()
    )
    skipped = (
        pd.DataFrame(skipped_rows).reset_index(drop=True)
        if skipped_rows
        else eligible.iloc[:0].copy()
    )
    selected["account_net_r"] = pd.to_numeric(
        selected.get("account_net_r"), errors="coerce"
    )
    closed = selected[selected["account_net_r"].notna()].copy().reset_index(drop=True)

    nav = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    nav_path: list[float] = []
    for result in closed["account_net_r"].astype(float):
        nav *= max(1e-12, 1.0 + RISK_FRACTION * result)
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        nav_path.append(nav)
    closed["account_nav"] = nav_path

    calendar_days = int(sum(item["calendar_days"] for item in metadata.values()))
    wins = closed["outcome"].astype(str).eq("TARGET_FIRST") if not closed.empty else pd.Series(dtype=bool)
    unresolved = selected[selected["account_net_r"].isna()].copy()
    summary = {
        "periods": period_order,
        "calendar_days": calendar_days,
        "eligible_actions": int(len(eligible)),
        "selected_orders": int(len(selected)),
        "closed_trades": int(len(closed)),
        "unresolved_selected_orders": int(len(unresolved)),
        "independent_closed_trades_per_day": float(len(closed) / max(calendar_days, 1)),
        "target_first_rate": float(wins.mean()) if len(closed) else None,
        "mean_account_r": float(closed["account_net_r"].mean()) if len(closed) else None,
        "median_account_r": float(closed["account_net_r"].median()) if len(closed) else None,
        "mean_planned_gross_rr": float(
            pd.to_numeric(closed.get("gross_rr"), errors="coerce").mean()
        ) if len(closed) else None,
        "mean_cost_inclusive_target_r": float(
            pd.to_numeric(closed.get("account_target_r"), errors="coerce").mean()
        ) if len(closed) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "risk_fraction_per_trade": RISK_FRACTION,
        "by_period": (
            closed.groupby("period")
            .agg(
                trades=("account_net_r", "size"),
                target_first_rate=("outcome", lambda x: float((x.astype(str) == "TARGET_FIRST").mean())),
                mean_account_r=("account_net_r", "mean"),
            )
            .reset_index()
            .to_dict("records")
            if len(closed)
            else []
        ),
        "by_family": (
            closed.groupby("scenario_family")
            .agg(
                trades=("account_net_r", "size"),
                target_first_rate=("outcome", lambda x: float((x.astype(str) == "TARGET_FIRST").mean())),
                mean_account_r=("account_net_r", "mean"),
            )
            .reset_index()
            .to_dict("records")
            if len(closed)
            else []
        ),
        "by_symbol": (
            closed.groupby("symbol")
            .agg(
                trades=("account_net_r", "size"),
                target_first_rate=("outcome", lambda x: float((x.astype(str) == "TARGET_FIRST").mean())),
                mean_account_r=("account_net_r", "mean"),
            )
            .reset_index()
            .to_dict("records")
            if len(closed)
            else []
        ),
        "continuous_account_complete": bool(len(unresolved) == 0),
        "filled_position_exit_contract": "TAKE_PROFIT_OR_STOP_LOSS_ONLY",
    }
    return selected, closed, pd.concat([rejected, skipped], ignore_index=True, sort=False), summary


def run_dataset(root: Path, output: Path, name: str) -> dict[str, Any]:
    frame, metadata = load_periods(root)
    scored = apply_policy(frame)
    selected, closed, rejected, summary = route_one_account(scored, metadata)
    output.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output / f"{name}_decisions.csv.gz", index=False, compression="gzip")
    selected.to_csv(output / f"{name}_orders.csv", index=False)
    closed.to_csv(output / f"{name}_trades.csv", index=False)
    rejected.to_csv(
        output / f"{name}_rejected_or_unselected.csv.gz",
        index=False,
        compression="gzip",
    )
    losses = closed[closed["account_net_r"] < 0.0].copy()
    losses.to_csv(output / f"{name}_losses.csv", index=False)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "policy": (
            "TWO_CAUSAL_FAMILIES:CONTROLLED_LIQUIDITY_DISPLACEMENT_FIRST_RETURN_"
            "OR_REGIME_ALIGNED_DERIVATIVES_DISLOCATION;ONE_CONTINUOUS_ACCOUNT"
        ),
        "development": run_dataset(
            args.development_root, args.output, "development"
        ),
    }
    if args.fresh_root:
        result["fresh"] = run_dataset(args.fresh_root, args.output, "fresh")

    (args.output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
