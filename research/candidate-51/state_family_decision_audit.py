#!/usr/bin/env python3
"""Decision audit for the recovered v56 squeeze and v57 impulse evidence.

This is not a parameter search and it does not promote the largest aggregate
mean. It asks whether each pre-existing causal claim survives chronology,
assets, tail removal and one-slot episode arbitration. Negative families are
still decomposed so reusable sub-mechanisms are not discarded with the whole.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

V56_HORIZONS = (60, 120, 240, 480, 720, 1440)
V57_HORIZONS = (120, 240, 480, 720)
V57_EVALUATION_DAYS = 140  # ten frozen inclusive 14-day evaluation windows


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    raise TypeError(type(value))


def _profit_factor(values: np.ndarray) -> float | None:
    gains = float(values[values > 0.0].sum())
    losses = float(-values[values < 0.0].sum())
    return None if losses <= 0.0 else gains / losses


def _stats(values: Iterable[Any]) -> dict[str, Any]:
    clean = pd.to_numeric(pd.Series(values), errors="coerce").dropna().to_numpy(float)
    if clean.size == 0:
        return {"count": 0}
    ordered = np.sort(clean)
    positives = clean[clean > 0.0]
    positive_sum = float(positives.sum())
    top = np.sort(positives)[::-1]
    without_best = clean[clean != clean.max()]
    if without_best.size == clean.size and clean.size:
        without_best = np.delete(clean, int(np.argmax(clean)))
    without_worst = clean[clean != clean.min()]
    if without_worst.size == clean.size and clean.size:
        without_worst = np.delete(clean, int(np.argmin(clean)))
    return {
        "count": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(np.median(clean)),
        "win_rate": float(np.mean(clean > 0.0)),
        "profit_factor": _profit_factor(clean),
        "gross_profit": positive_sum,
        "gross_loss": float(-clean[clean < 0.0].sum()),
        "q10": float(np.quantile(clean, 0.10)),
        "q90": float(np.quantile(clean, 0.90)),
        "best": float(ordered[-1]),
        "worst": float(ordered[0]),
        "top1_positive_share": None if positive_sum <= 0.0 else float(top[:1].sum() / positive_sum),
        "top2_positive_share": None if positive_sum <= 0.0 else float(top[:2].sum() / positive_sum),
        "mean_without_best": None if without_best.size == 0 else float(without_best.mean()),
        "mean_without_worst": None if without_worst.size == 0 else float(without_worst.mean()),
    }


def _bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.lower().map({"true": True, "false": False}).fillna(False)


def _named_groups(frame: pd.DataFrame, specifications: list[tuple[str, list[str]]]):
    yield "all", frame
    for prefix, columns in specifications:
        for key, group in frame.groupby(columns, sort=True, dropna=False):
            values = key if isinstance(key, tuple) else (key,)
            label = ":".join(str(value) for value in values)
            yield f"{prefix}:{label}", group


def _period_robustness(frame: pd.DataFrame, value_column: str) -> dict[str, Any]:
    period_means = (
        frame.groupby(["split", "period_label"], sort=True)[value_column]
        .mean()
        .dropna()
    )
    if period_means.empty:
        return {"periods": 0}
    overall = pd.to_numeric(frame[value_column], errors="coerce").dropna()
    loo = []
    for split_period in period_means.index:
        split, period = split_period
        retained = frame[~((frame["split"] == split) & (frame["period_label"] == period))]
        values = pd.to_numeric(retained[value_column], errors="coerce").dropna()
        if len(values):
            loo.append(float(values.mean()))
    return {
        "periods": int(len(period_means)),
        "positive_period_share": float((period_means > 0.0).mean()),
        "period_mean_min": float(period_means.min()),
        "period_mean_median": float(period_means.median()),
        "period_mean_max": float(period_means.max()),
        "overall_mean": None if overall.empty else float(overall.mean()),
        "leave_one_period_out_min": None if not loo else float(min(loo)),
        "leave_one_period_out_max": None if not loo else float(max(loo)),
    }


def _v56(candidate: Path) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    path = candidate / "evidence" / "squeeze-clock-v56" / "EVENTS.csv"
    events = pd.read_csv(path)
    for column in ("event_time", "entry_time"):
        events[column] = pd.to_datetime(events[column], utc=True, errors="raise")
    events["outside_band"] = _bool_series(events["outside_band"])
    causal = events[events["clock"].eq("causal_completed")].copy()
    if causal["event_id"].duplicated().any():
        raise RuntimeError("v56 causal event IDs must be unique")

    rows: list[dict[str, Any]] = []
    groups = list(
        _named_groups(
            causal,
            [
                ("split", ["split"]),
                ("period", ["period_label"]),
                ("asset", ["symbol"]),
                ("entry_mode", ["outside_band"]),
                ("side", ["side"]),
                ("asset_entry", ["symbol", "outside_band"]),
                ("split_entry", ["split", "outside_band"]),
                ("split_asset", ["split", "symbol"]),
            ],
        )
    )
    for name, group in groups:
        for horizon in V56_HORIZONS:
            column = f"net19_{horizon}m"
            rows.append(
                {
                    "group": name,
                    "horizon_min": horizon,
                    **_stats(group[column]),
                }
            )
    group_frame = pd.DataFrame(rows)

    split_horizon: dict[str, Any] = {}
    for horizon in V56_HORIZONS:
        column = f"net19_{horizon}m"
        split_horizon[str(horizon)] = {
            str(split): _stats(group[column])
            for split, group in causal.groupby("split", sort=True)
        }
        split_horizon[str(horizon)]["period_robustness"] = _period_robustness(causal, column)

    entry_modes: dict[str, Any] = {}
    for outside, group in causal.groupby("outside_band", sort=True):
        key = "outside_band" if bool(outside) else "momentum_fallback"
        entry_modes[key] = {
            str(horizon): _stats(group[f"net19_{horizon}m"])
            for horizon in V56_HORIZONS
        }
        entry_modes[key]["post_publication"] = {
            str(horizon): _stats(
                group[group["split"].eq("post_publication")][f"net19_{horizon}m"]
            )
            for horizon in V56_HORIZONS
        }

    post = causal[causal["split"].eq("post_publication")]
    post_metrics = {
        str(horizon): _stats(post[f"net19_{horizon}m"])
        for horizon in V56_HORIZONS
    }
    historical = causal[causal["split"].ne("post_publication")]
    historical_metrics = {
        str(horizon): _stats(historical[f"net19_{horizon}m"])
        for horizon in V56_HORIZONS
    }

    failures = []
    for horizon in V56_HORIZONS:
        post_stat = post_metrics[str(horizon)]
        history_stat = historical_metrics[str(horizon)]
        if post_stat.get("mean", 0.0) <= 0.0:
            failures.append(f"{horizon}m post-publication mean is non-positive")
        if history_stat.get("mean", 0.0) > 0.0 and post_stat.get("mean", 0.0) <= 0.0:
            failures.append(f"{horizon}m sign flips from source history to post-publication")

    decision = {
        "family": "causal_completed_4h_squeeze_release_continuation",
        "observations": int(len(causal)),
        "periods": int(causal["period_label"].nunique()),
        "historical": historical_metrics,
        "post_publication": post_metrics,
        "entry_modes": entry_modes,
        "status": "not_promoted_as_is" if failures else "requires_executable_validation",
        "reasons": failures,
        "interpretation": (
            "A positive pooled mean cannot override chronological sign reversal. "
            "Pre-existing outside-band and momentum-fallback modes remain separately auditable; "
            "no numeric threshold was fitted."
        ),
    }

    audit_columns = [
        "event_id", "event_time", "entry_time", "period_label", "split", "symbol",
        "side", "outside_band", "squeeze_bars", "volume_ratio", "momentum_norm",
        "risk_fraction", *[f"net19_{h}m" for h in V56_HORIZONS],
        "outcome_720m", "outcome_1440m", "r_720m", "r_1440m",
    ]
    return decision, group_frame, causal[audit_columns].copy()


def _deduplicate_v57(events: pd.DataFrame) -> pd.DataFrame:
    events = events.copy()
    events["episode_id"] = (
        events["symbol"].astype(str) + ":" + events["event_time"].astype(str)
    )
    family_priority = {"public_vectorized_no_ema": 0, "impulse_only_2atr": 1}
    events["family_priority"] = events["family"].map(family_priority).fillna(9)
    ordered = events.sort_values(
        ["episode_id", "family_priority", "impulse_atr", "breadth"],
        ascending=[True, True, False, False],
        kind="stable",
    )
    return ordered.drop_duplicates("episode_id", keep="first").copy()


def _one_slot(frame: pd.DataFrame, horizon: int, entry_column: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    ordered = frame.sort_values(
        [entry_column, "impulse_atr", "breadth", "symbol"],
        ascending=[True, False, False, True],
        kind="stable",
    )
    selected: list[int] = []
    occupied_until: pd.Timestamp | None = None
    for idx, row in ordered.iterrows():
        entry = pd.Timestamp(row[entry_column])
        if occupied_until is not None and entry < occupied_until:
            continue
        selected.append(idx)
        occupied_until = entry + pd.Timedelta(minutes=horizon)
    return frame.loc[selected].copy()


def _route_status(overall: dict[str, Any], split_stats: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if overall.get("mean", 0.0) <= 0.0 or (overall.get("profit_factor") or 0.0) <= 1.0:
        reasons.append("aggregate intended-policy expectancy is non-positive")
    positive_splits = sum(
        1 for item in split_stats.values() if item.get("count", 0) and item.get("mean", 0.0) > 0.0
    )
    populated = sum(1 for item in split_stats.values() if item.get("count", 0))
    if populated and positive_splits < populated:
        reasons.append(f"chronological sign instability ({positive_splits}/{populated} positive splits)")
    post = split_stats.get("post_publication", {})
    if post.get("count", 0) and post.get("mean", 0.0) <= 0.0:
        reasons.append("post-publication mean is non-positive")
    if (overall.get("top1_positive_share") or 0.0) > 0.50:
        reasons.append("one event supplies more than half of gross positive return")
    if overall.get("mean_without_best") is not None and overall["mean_without_best"] <= 0.0:
        reasons.append("mean becomes non-positive after removing the best event")
    if not reasons:
        return "provisional_mechanism_signal", []
    if reasons[0] == "aggregate intended-policy expectancy is non-positive":
        return "causal_prediction_contradicted", reasons
    return "unstable_or_tail_fragile", reasons


def _v57(candidate: Path) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    path = candidate / "evidence" / "derivatives-impulse-v57" / "EVENTS.csv"
    raw = pd.read_csv(path)
    for column in ("event_time", "entry_time", "delayed_entry_time"):
        raw[column] = pd.to_datetime(raw[column], utc=True, errors="raise")
    episodes = _deduplicate_v57(raw)

    policy_columns = {
        "direct_continuation": ("entry_time", "cont_{h}m"),
        "delayed_continuation": ("delayed_entry_time", "delayed_cont_{h}m"),
        "delayed_reversal": ("delayed_entry_time", "delayed_rev_{h}m"),
    }
    rows: list[dict[str, Any]] = []
    group_specs = [
        ("state", ["state"]),
        ("transition", ["transition15"]),
        ("split", ["split"]),
        ("asset", ["symbol"]),
        ("family", ["family"]),
        ("split_state", ["split", "state"]),
        ("split_transition", ["split", "transition15"]),
        ("asset_state", ["symbol", "state"]),
        ("state_transition", ["state", "transition15"]),
    ]
    groups = list(_named_groups(episodes, group_specs))
    for name, group in groups:
        for policy, (_, template) in policy_columns.items():
            for horizon in V57_HORIZONS:
                rows.append(
                    {
                        "group": name,
                        "policy": policy,
                        "horizon_min": horizon,
                        **_stats(group[template.format(h=horizon)]),
                    }
                )
    group_frame = pd.DataFrame(rows)

    route_specs = {
        "sponsored_build_direct_continuation": {
            "mask": episodes["state"].str.startswith("SPONSORED_BUILD"),
            "entry": "entry_time", "column": "cont_{h}m",
            "prediction": "new OI build with aligned taker and premium should continue",
        },
        "sponsored_build_delayed_continuation": {
            "mask": episodes["state"].str.startswith("SPONSORED_BUILD"),
            "entry": "delayed_entry_time", "column": "delayed_cont_{h}m",
            "prediction": "sponsored build should retain target space after 15m confirmation",
        },
        "forced_unwind_accepted_direct_continuation": {
            "mask": episodes["state"].eq("FORCED_UNWIND_ACCEPTED"),
            "entry": "entry_time", "column": "cont_{h}m",
            "prediction": "accepted forced unwind should continue over the first leg",
        },
        "forced_unwind_accepted_delayed_continuation": {
            "mask": episodes["state"].eq("FORCED_UNWIND_ACCEPTED"),
            "entry": "delayed_entry_time", "column": "delayed_cont_{h}m",
            "prediction": "accepted forced unwind should remain tradable after 15m",
        },
        "forced_unwind_rejected_delayed_reversal": {
            "mask": episodes["state"].eq("FORCED_UNWIND_REJECTED"),
            "entry": "delayed_entry_time", "column": "delayed_rev_{h}m",
            "prediction": "rejected forced unwind should reverse after the rejection is observable",
        },
        "persistent_15_delayed_continuation": {
            "mask": episodes["transition15"].eq("PERSISTENT_15"),
            "entry": "delayed_entry_time", "column": "delayed_cont_{h}m",
            "prediction": "price and taker persistence through +15m should continue",
        },
        "rejected_15_delayed_reversal": {
            "mask": episodes["transition15"].eq("REJECTED_15"),
            "entry": "delayed_entry_time", "column": "delayed_rev_{h}m",
            "prediction": "price and taker rejection through +15m should reverse",
        },
    }

    routes: dict[str, Any] = {}
    for route_name, spec in route_specs.items():
        route = episodes[spec["mask"]].copy()
        route_payload: dict[str, Any] = {
            "prediction": spec["prediction"],
            "episodes": int(len(route)),
            "horizons": {},
        }
        for horizon in V57_HORIZONS:
            value_column = spec["column"].format(h=horizon)
            overall = _stats(route[value_column])
            split_stats = {
                str(split): _stats(group[value_column])
                for split, group in route.groupby("split", sort=True)
            }
            asset_stats = {
                str(symbol): _stats(group[value_column])
                for symbol, group in route.groupby("symbol", sort=True)
            }
            one_slot = _one_slot(route, horizon, spec["entry"])
            slot_stats = _stats(one_slot[value_column])
            status, reasons = _route_status(overall, split_stats)
            route_payload["horizons"][str(horizon)] = {
                "overall": overall,
                "by_split": split_stats,
                "by_asset": asset_stats,
                "period_robustness": _period_robustness(route, value_column),
                "one_slot": {
                    **slot_stats,
                    "trades": int(len(one_slot)),
                    "trades_per_evaluation_day": float(len(one_slot) / V57_EVALUATION_DAYS),
                },
                "status": status,
                "reasons": reasons,
            }
        routes[route_name] = route_payload

    provisional = []
    for route_name, payload in routes.items():
        for horizon, item in payload["horizons"].items():
            if item["status"] == "provisional_mechanism_signal":
                provisional.append({"route": route_name, "horizon_min": int(horizon)})

    decision = {
        "raw_signal_records": int(len(raw)),
        "unique_causal_episodes": int(len(episodes)),
        "duplicate_family_records_removed": int(len(raw) - len(episodes)),
        "evaluation_days": V57_EVALUATION_DAYS,
        "routes": routes,
        "provisional_routes": provisional,
        "status": "route_exists_for_scenario_geometry" if provisional else "state_model_not_promoted",
        "interpretation": (
            "The same price impulse is counted once per causal episode before one-slot routing. "
            "A route is not deployment-ready even when provisionally coherent; it only earns a "
            "minimal entry/invalidation/target geometry test in NautilusTrader."
        ),
    }

    audit_columns = [
        "episode_id", "event_time", "entry_time", "delayed_entry_time", "period_label",
        "split", "symbol", "family", "side", "state", "transition15", "oi_mode",
        "oi_material", "taker_aligned", "premium_aligned", "accepted_last15",
        "breadth", "impulse_breadth", "impulse_atr", "last15_aligned_return",
        *[f"cont_{h}m" for h in V57_HORIZONS],
        *[f"delayed_cont_{h}m" for h in V57_HORIZONS],
        *[f"delayed_rev_{h}m" for h in V57_HORIZONS],
    ]
    return decision, group_frame, episodes[audit_columns].copy()


def _fmt_bp(item: dict[str, Any]) -> str:
    if not item or not item.get("count"):
        return "na"
    return f"{10000.0 * item.get('mean', 0.0):.2f}"


def _markdown(v56: dict[str, Any], v57: dict[str, Any]) -> str:
    lines = [
        "# Causal state-family decision audit", "",
        "This audit does not rank parameter combinations. It checks whether frozen causal claims survive chronology, assets, tail removal and one-slot episode arbitration.", "",
        "## v56 recovered 4h squeeze release", "",
        f"- causal observations: {v56['observations']}",
        f"- status: **{v56['status']}**",
    ]
    for reason in v56["reasons"]:
        lines.append(f"- {reason}")
    lines += ["", "| horizon | historical mean bp | post-publication mean bp | post PF |",
              "|---:|---:|---:|---:|"]
    for horizon in V56_HORIZONS:
        hist = v56["historical"][str(horizon)]
        post = v56["post_publication"][str(horizon)]
        pf = post.get("profit_factor")
        lines.append(
            f"| {horizon}m | {_fmt_bp(hist)} | {_fmt_bp(post)} | "
            f"{'na' if pf is None else f'{pf:.2f}'} |"
        )
    lines += ["", "Pre-existing entry modes are retained in the JSON/CSV audit. A pooled historical win is not promoted when the same causal policy reverses sign after publication.", "",
              "## v57 derivatives sponsorship and transition routes", "",
              f"- raw signal records: {v57['raw_signal_records']}",
              f"- unique causal episodes: {v57['unique_causal_episodes']}",
              f"- status: **{v57['status']}**", "",
              "| route | horizon | episodes | mean bp | post-publication bp | one-slot trades/day | status |",
              "|---|---:|---:|---:|---:|---:|---|"]
    for route_name, payload in v57["routes"].items():
        for horizon in V57_HORIZONS:
            item = payload["horizons"][str(horizon)]
            post = item["by_split"].get("post_publication", {})
            lines.append(
                f"| {route_name} | {horizon}m | {payload['episodes']} | "
                f"{_fmt_bp(item['overall'])} | {_fmt_bp(post)} | "
                f"{item['one_slot']['trades_per_evaluation_day']:.3f} | {item['status']} |"
            )
    lines += ["", "## Decision contract", "",
              "A negative aggregate does not erase useful components, but a causal prediction is not accepted merely because another unintended subgroup made money. A candidate route must preserve its predicted direction across chronological partitions, remain after the best episode is removed, and then survive executable scenario geometry and continuous-account arbitration.", ""]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> None:
    candidate = Path(args.candidate_dir)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    v56, v56_groups, v56_events = _v56(candidate)
    v57, v57_groups, v57_events = _v57(candidate)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "v56": v56,
        "v57": v57,
    }
    (output / "DECISION.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    (output / "DECISION.md").write_text(_markdown(v56, v57), encoding="utf-8")
    v56_groups.to_csv(output / "V56_GROUPS.csv", index=False)
    v57_groups.to_csv(output / "V57_GROUPS.csv", index=False)
    v56_events.to_csv(output / "V56_EVENTS_AUDIT.csv", index=False)
    v57_events.to_csv(output / "V57_EVENTS_AUDIT.csv", index=False)
    print(json.dumps({"v56": v56["status"], "v57": v57["status"], "provisional": v57["provisional_routes"]}, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--candidate-dir", default="research/candidate-51")
    root.add_argument("--output", required=True)
    root.set_defaults(func=run)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
