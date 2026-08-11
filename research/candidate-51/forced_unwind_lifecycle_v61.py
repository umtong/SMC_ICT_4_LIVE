#!/usr/bin/env python3
"""Causal lifecycle repair for the frozen accepted forced-unwind geometry.

This audit does not search thresholds or geometries.  It starts from the exact
v59 configuration already selected before this audit:

    direct entry / impulse-origin invalidation / 2R objective / 480m maximum

It tests three implementation and management questions which the v59 aggregate
did not represent faithfully:

1. A global slot should be released at the actual stop/target/time exit, not at
   the maximum 480-minute horizon.
2. Re-entry into another hourly signal from the same market-wide forced-unwind
   episode must not inflate opportunity count.  An episode remains active until
   two completed hourly state clocks pass without another accepted event in the
   same direction.
3. A direct entry which is still open after the completed 15-minute transition
   should be exited at that causal observation if the transition is explicitly
   REJECTED_15.  MIXED_15 is not changed.

The expected result was declared before the comparison: actual lifecycle should
recover only genuinely available slots; the episode lock should remove cascade
repeats; REJECTED_15 exits should truncate the identified rejection loss group
without deleting PERSISTENT_15 winners.  Failure to do those things falsifies
the repair.  This remains a path-level diagnostic, not a matching/account/NAV
engine and not final evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

CONFIG = {
    "entry_mode": "direct",
    "stop_mode": "impulse_origin",
    "target_mode": "two_r",
    "hold_min": 480,
}
EVALUATION_DAYS = 140
EPISODE_GAP_MINUTES = 120
RISK_FRACTION = 0.03
POLICY_ORDER = (
    "v59_fixed_hold_baseline",
    "actual_exit_only",
    "episode_locked_actual_exit",
    "episode_locked_rejected15_exit",
)


def _finite(series: pd.Series) -> np.ndarray:
    return (
        pd.to_numeric(series, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .to_numpy(float)
    )


def _summary(series: pd.Series) -> dict[str, Any]:
    values = _finite(series)
    if values.size == 0:
        return {"count": 0}
    gains = float(values[values > 0.0].sum())
    losses = float(-values[values < 0.0].sum())
    without_best = (
        np.delete(values, int(np.argmax(values)))
        if values.size > 1
        else np.array([], dtype=float)
    )
    return {
        "count": int(values.size),
        "mean": float(values.mean()),
        "median": float(np.median(values)),
        "win_rate": float(np.mean(values > 0.0)),
        "profit_factor": None if losses <= 0.0 else gains / losses,
        "gross_profit": gains,
        "gross_loss": losses,
        "best": float(values.max()),
        "worst": float(values.min()),
        "mean_without_best": (
            None if without_best.size == 0 else float(without_best.mean())
        ),
    }


def _nav(series: pd.Series) -> dict[str, Any]:
    multiples = _finite(series)
    if multiples.size == 0:
        return {"trades": 0}
    returns = RISK_FRACTION * multiples
    if np.any(1.0 + returns <= 0.0):
        raise RuntimeError("diagnostic NAV became non-positive")
    nav = np.concatenate(([1.0], np.cumprod(1.0 + returns)))
    peak = np.maximum.accumulate(nav)
    drawdown = nav / peak - 1.0
    return {
        "trades": int(multiples.size),
        "final_nav_multiple": float(nav[-1]),
        "daily_geometric_growth_over_140_sampled_days": float(
            nav[-1] ** (1.0 / EVALUATION_DAYS) - 1.0
        ),
        "max_drawdown": float(drawdown.min()),
        "minimum_trade_nav_return": float(returns.min()),
        "maximum_trade_nav_return": float(returns.max()),
        "note": (
            "Diagnostic compounding of frozen path R at NAV x 3%; the sampled "
            "periods are not a continuous NautilusTrader account."
        ),
    }


def _group_summaries(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty or column not in frame.columns:
        return {}
    return {
        str(key): _summary(group["policy_r"])
        for key, group in frame.groupby(column, dropna=False, sort=True)
    }


def _assign_market_episodes(frame: pd.DataFrame) -> pd.DataFrame:
    """Group cross-asset hourly states into outcome-blind market episodes."""
    out = frame.copy()
    clocks = (
        out[["period_label", "event_time", "side"]]
        .drop_duplicates()
        .sort_values(["period_label", "event_time", "side"], kind="stable")
    )
    mapping: dict[tuple[str, pd.Timestamp, int], str] = {}
    for period, group in clocks.groupby("period_label", sort=False):
        episode = -1
        previous_time: pd.Timestamp | None = None
        previous_side: int | None = None
        for row in group.itertuples(index=False):
            event_time = pd.Timestamp(row.event_time)
            side = int(row.side)
            if (
                previous_time is None
                or previous_side != side
                or event_time - previous_time
                > pd.Timedelta(minutes=EPISODE_GAP_MINUTES)
            ):
                episode += 1
            mapping[(str(period), event_time, side)] = (
                f"{period}:{side:+d}:{episode:03d}"
            )
            previous_time = event_time
            previous_side = side
    out["market_episode_id"] = [
        mapping[(str(period), pd.Timestamp(event_time), int(side))]
        for period, event_time, side in zip(
            out["period_label"], out["event_time"], out["side"], strict=True
        )
    ]
    return out


def _choose_at_event_clock(group: pd.DataFrame) -> pd.Series:
    """Reuse the exact v59 outcome-blind same-clock arbitration."""
    return group.sort_values(
        ["impulse_atr", "breadth", "symbol"],
        ascending=[False, False, True],
        kind="stable",
    ).iloc[0]


def _candidate_stream(frame: pd.DataFrame) -> pd.DataFrame:
    rows = [
        _choose_at_event_clock(group)
        for _, group in frame.groupby(
            ["period_label", "event_time"], sort=True, dropna=False
        )
    ]
    return (
        pd.DataFrame(rows)
        .sort_values(["entry_time_geometry", "period_label"], kind="stable")
        .reset_index(drop=True)
    )


def _apply_rejection_exit(frame: pd.DataFrame, enabled: bool) -> pd.DataFrame:
    out = frame.copy()
    out["policy_r"] = pd.to_numeric(out["r_multiple"], errors="coerce")
    out["policy_net"] = pd.to_numeric(out["net_fraction"], errors="coerce")
    out["policy_exit_time"] = out["exit_time_geometry"]
    out["policy_exit_reason"] = out["outcome"].astype(str)
    out["rejection_exit_applied"] = False
    if not enabled:
        return out

    # At delayed_entry_time the completed 15-minute transition is observable.
    # If a stop/target happened earlier, the original exit remains authoritative.
    eligible = (
        out["transition15"].eq("REJECTED_15")
        & out["delayed_entry_time"].notna()
        & out["cont_15m"].notna()
        & (out["exit_time_geometry"] >= out["delayed_entry_time"])
    )
    out.loc[eligible, "policy_net"] = pd.to_numeric(
        out.loc[eligible, "cont_15m"], errors="coerce"
    )
    out.loc[eligible, "policy_r"] = (
        out.loc[eligible, "policy_net"]
        / pd.to_numeric(
            out.loc[eligible, "planned_loss_fraction"], errors="coerce"
        )
    )
    out.loc[eligible, "policy_exit_time"] = out.loc[
        eligible, "delayed_entry_time"
    ]
    out.loc[eligible, "policy_exit_reason"] = "REJECTED_15_EXIT"
    out.loc[eligible, "rejection_exit_applied"] = True
    return out


def _select_policy(
    candidates: pd.DataFrame,
    *,
    fixed_hold: bool,
    episode_lock: bool,
    rejection_exit: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = _apply_rejection_exit(candidates, rejection_exit)
    selected: list[int] = []
    rejected_rows: list[dict[str, Any]] = []
    occupied_until: pd.Timestamp | None = None
    used_episodes: set[str] = set()

    for index, row in work.sort_values(
        ["entry_time_geometry", "event_time", "symbol"], kind="stable"
    ).iterrows():
        entry = pd.Timestamp(row["entry_time_geometry"])
        episode = str(row["market_episode_id"])
        if occupied_until is not None and entry < occupied_until:
            rejected_rows.append(
                {**row.to_dict(), "policy_rejection_reason": "GLOBAL_SLOT_OCCUPIED"}
            )
            continue
        if episode_lock and episode in used_episodes:
            rejected_rows.append(
                {**row.to_dict(), "policy_rejection_reason": "CAUSAL_EPISODE_REPEAT"}
            )
            continue

        selected.append(index)
        used_episodes.add(episode)
        if fixed_hold:
            occupied_until = entry + pd.Timedelta(minutes=CONFIG["hold_min"])
        else:
            occupied_until = pd.Timestamp(row["policy_exit_time"])

    selected_frame = work.loc[selected].copy()
    rejected_frame = pd.DataFrame(rejected_rows)
    return selected_frame, rejected_frame


def _policy_result(
    selected: pd.DataFrame,
    rejected: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "selected_trades": int(len(selected)),
        "selected_trades_per_sampled_day": float(
            len(selected) / EVALUATION_DAYS
        ),
        "independent_market_episodes": int(
            selected["market_episode_id"].nunique()
        ),
        "r_multiple": _summary(selected["policy_r"]),
        "net_fraction": _summary(selected["policy_net"]),
        "diagnostic_nav": _nav(selected["policy_r"]),
        "by_split_r": _group_summaries(selected, "split"),
        "by_period_r": _group_summaries(selected, "period_label"),
        "by_symbol_r": _group_summaries(selected, "symbol"),
        "by_transition_r": _group_summaries(selected, "transition15"),
        "exit_reasons": (
            selected["policy_exit_reason"]
            .value_counts()
            .sort_index()
            .astype(int)
            .to_dict()
        ),
        "rejection_exit_count": int(
            selected["rejection_exit_applied"].sum()
        ),
        "rejected_candidates": int(len(rejected)),
        "rejection_reasons": (
            {}
            if rejected.empty
            else rejected["policy_rejection_reason"]
            .value_counts()
            .sort_index()
            .astype(int)
            .to_dict()
        ),
    }


def _fmt(value: Any, scale: float = 1.0, digits: int = 3) -> str:
    if value is None:
        return "na"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "na"
    return f"{number * scale:.{digits}f}"


def run(args: argparse.Namespace) -> None:
    source = Path(args.input)
    output = Path(args.output)
    records = pd.read_csv(source, low_memory=False)
    mask = (
        records["entry_mode"].eq(CONFIG["entry_mode"])
        & records["stop_mode"].eq(CONFIG["stop_mode"])
        & records["target_mode"].eq(CONFIG["target_mode"])
        & pd.to_numeric(records["hold_min"], errors="coerce").eq(
            CONFIG["hold_min"]
        )
    )
    frame = records.loc[mask].copy()
    if frame.empty:
        raise RuntimeError("frozen v59 configuration not found")

    for column in (
        "event_time",
        "entry_time_geometry",
        "exit_time_geometry",
        "delayed_entry_time",
    ):
        frame[column] = pd.to_datetime(
            frame[column], utc=True, errors="coerce"
        )
    frame = frame.sort_values(
        ["event_time", "symbol", "impulse_atr"],
        ascending=[True, True, False],
        kind="stable",
    ).drop_duplicates("causal_episode_id", keep="first")
    frame = _assign_market_episodes(frame)
    candidates = _candidate_stream(frame)

    policy_specs = {
        "v59_fixed_hold_baseline": {
            "fixed_hold": True,
            "episode_lock": False,
            "rejection_exit": False,
        },
        "actual_exit_only": {
            "fixed_hold": False,
            "episode_lock": False,
            "rejection_exit": False,
        },
        "episode_locked_actual_exit": {
            "fixed_hold": False,
            "episode_lock": True,
            "rejection_exit": False,
        },
        "episode_locked_rejected15_exit": {
            "fixed_hold": False,
            "episode_lock": True,
            "rejection_exit": True,
        },
    }

    selected_outputs: list[pd.DataFrame] = []
    rejected_outputs: list[pd.DataFrame] = []
    results: dict[str, Any] = {}
    policy_frames: dict[str, pd.DataFrame] = {}

    for name in POLICY_ORDER:
        selected, rejected = _select_policy(
            candidates, **policy_specs[name]
        )
        selected["policy"] = name
        if not rejected.empty:
            rejected["policy"] = name
        selected_outputs.append(selected)
        rejected_outputs.append(rejected)
        policy_frames[name] = selected
        results[name] = _policy_result(selected, rejected)

    baseline = results["v59_fixed_hold_baseline"]
    reproduced = (
        baseline["selected_trades"] == 39
        and abs(baseline["r_multiple"]["mean"] - 0.3824526791008725)
        < 1e-12
    )
    if not reproduced:
        raise RuntimeError(
            "v59 baseline reproduction failed; lifecycle attribution invalid"
        )

    actual = results["actual_exit_only"]
    locked = results["episode_locked_actual_exit"]
    managed = results["episode_locked_rejected15_exit"]

    rejected_before = policy_frames["episode_locked_actual_exit"]
    rejected_after = policy_frames["episode_locked_rejected15_exit"]
    before_map = rejected_before.set_index("causal_episode_id")["policy_r"]
    after_map = rejected_after.set_index("causal_episode_id")["policy_r"]
    common = before_map.index.intersection(after_map.index)
    changed = (
        pd.DataFrame(
            {
                "before_r": before_map.reindex(common),
                "after_r": after_map.reindex(common),
            }
        )
        .query("before_r != after_r")
        .copy()
    )
    changed["delta_r"] = changed["after_r"] - changed["before_r"]

    assessment = {
        "actual_exit_recovers_available_slots": (
            actual["selected_trades"] > baseline["selected_trades"]
            and actual["r_multiple"].get("mean_without_best", -math.inf) > 0.0
        ),
        "episode_lock_removes_repeats_without_destroying_edge": (
            locked["r_multiple"].get("mean_without_best", -math.inf) > 0.0
            and locked["r_multiple"].get("profit_factor", 0.0) > 1.0
        ),
        "rejected15_exit_truncates_the_predicted_loss_group": (
            not changed.empty
            and float(changed["delta_r"].sum()) > 0.0
            and managed["diagnostic_nav"]["max_drawdown"]
            > locked["diagnostic_nav"]["max_drawdown"]
        ),
        "persistent15_winners_are_unchanged": True,
    }

    # Explicitly verify the final claim rather than infer it from the policy name.
    persistent_before = rejected_before[
        rejected_before["transition15"].eq("PERSISTENT_15")
    ].set_index("causal_episode_id")["policy_r"]
    persistent_after = rejected_after[
        rejected_after["transition15"].eq("PERSISTENT_15")
    ].set_index("causal_episode_id")["policy_r"]
    persistent_common = persistent_before.index.intersection(
        persistent_after.index
    )
    assessment["persistent15_winners_are_unchanged"] = bool(
        np.allclose(
            persistent_before.reindex(persistent_common).to_numpy(float),
            persistent_after.reindex(persistent_common).to_numpy(float),
            equal_nan=True,
        )
    )

    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "frozen_configuration": CONFIG,
        "evaluation_days": EVALUATION_DAYS,
        "risk_fraction_for_diagnostic_compounding": RISK_FRACTION,
        "episode_contract": {
            "clock": "completed hourly FORCED_UNWIND_ACCEPTED event time",
            "cross_asset": True,
            "same_direction_gap_minutes": EPISODE_GAP_MINUTES,
            "episode_ends": (
                "direction changes or more than two completed hourly state "
                "clocks pass without another accepted event"
            ),
            "outcome_blind": True,
        },
        "management_hypothesis": {
            "observation_time": "completed +15 minute transition",
            "repair": (
                "if still open and transition15 == REJECTED_15, exit at the "
                "next causal open represented by delayed_entry_price"
            ),
            "unchanged": [
                "entry",
                "impulse-origin invalidation before the observation",
                "2R objective",
                "480 minute maximum hold",
                "MIXED_15 management",
                "PERSISTENT_15 management",
                "same-clock arbitration",
                "19 bp path cost",
            ],
            "falsification": (
                "The repair fails if actual exits do not recover genuine slots, "
                "if episode locking destroys ex-best expectancy, if rejected "
                "exits do not improve the pre-identified rejection group, or "
                "if persistent winners change."
            ),
        },
        "candidate_events": int(len(frame)),
        "same_clock_arbitrated_candidates": int(len(candidates)),
        "market_episodes": int(frame["market_episode_id"].nunique()),
        "policies": results,
        "changed_rejected15_trades": {
            "count": int(len(changed)),
            "sum_delta_r": (
                None if changed.empty else float(changed["delta_r"].sum())
            ),
            "mean_delta_r": (
                None if changed.empty else float(changed["delta_r"].mean())
            ),
        },
        "hypothesis_assessment": assessment,
        "diagnostic_conclusion": (
            "lifecycle_and_rejection_repair_supported"
            if all(assessment.values())
            else "lifecycle_or_rejection_hypothesis_not_fully_supported"
        ),
        "truth_boundary": (
            "This audit corrects lifecycle and episode accounting for one "
            "low-frequency specialist. It does not satisfy the final frequency "
            "or continuous NautilusTrader NAV requirement."
        ),
    }

    output.mkdir(parents=True, exist_ok=True)
    (output / "LIFECYCLE.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    pd.concat(selected_outputs, ignore_index=True).to_csv(
        output / "SELECTED.csv", index=False
    )
    nonempty_rejected = [
        value for value in rejected_outputs if not value.empty
    ]
    (
        pd.concat(nonempty_rejected, ignore_index=True)
        if nonempty_rejected
        else pd.DataFrame()
    ).to_csv(output / "REJECTED.csv", index=False)
    frame.to_csv(output / "EPISODE_CANDIDATES.csv", index=False)

    lines = [
        "# Accepted forced-unwind causal lifecycle v61",
        "",
        f"- frozen configuration: `{CONFIG}`",
        f"- raw asset episodes: {len(frame)}",
        f"- same-clock candidates after existing arbitration: {len(candidates)}",
        f"- market-wide causal episodes: {frame['market_episode_id'].nunique()}",
        f"- v59 reproduction: `{reproduced}`",
        f"- conclusion: **{payload['diagnostic_conclusion']}**",
        "",
        "| policy | trades | trades/day | mean R | median R | PF | ex-best R | final diagnostic NAV | daily geom | max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in POLICY_ORDER:
        value = results[name]
        r = value["r_multiple"]
        nav = value["diagnostic_nav"]
        lines.append(
            f"| {name} | {value['selected_trades']} | "
            f"{value['selected_trades_per_sampled_day']:.3f} | "
            f"{_fmt(r.get('mean'))} | {_fmt(r.get('median'))} | "
            f"{_fmt(r.get('profit_factor'), digits=2)} | "
            f"{_fmt(r.get('mean_without_best'))} | "
            f"{_fmt(nav.get('final_nav_multiple'))} | "
            f"{_fmt(nav.get('daily_geometric_growth_over_140_sampled_days'), 100, 3)}% | "
            f"{_fmt(nav.get('max_drawdown'), 100, 2)}% |"
        )
    lines += [
        "",
        "## Predeclared hypothesis assessment",
        "",
    ]
    for key, value in assessment.items():
        lines.append(f"- {key}: `{value}`")
    lines += [
        "",
        "## Truth boundary",
        "",
        payload["truth_boundary"],
        "",
    ]
    (output / "LIFECYCLE.md").write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
