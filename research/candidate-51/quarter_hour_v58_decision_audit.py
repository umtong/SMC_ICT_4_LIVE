#!/usr/bin/env python3
"""Decision audit for the frozen v58 quarter-hour experiment.

The aggregate family failed.  One predeclared diagnostic bin, absolute opening
imbalance 0.50--0.75, had a positive pooled 8--12h mean.  This audit does not
retune the boundary.  It asks whether that already-frozen bin survives every
period, the post-publication partition, assets, one-slot routing and removal of
the best event.  The result determines whether a genuinely new untouched test
is warranted or the entire family is discarded.
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

HORIZONS = (240, 480, 720)
BINS = ("0_025", "025_050", "050_075", "075_100")
EVALUATION_DAYS = 9


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
    positive = np.sort(clean[clean > 0.0])[::-1]
    positive_sum = float(positive.sum())
    without_best = np.delete(clean, int(np.argmax(clean))) if clean.size > 1 else np.array([])
    without_worst = np.delete(clean, int(np.argmin(clean))) if clean.size > 1 else np.array([])
    return {
        "count": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(np.median(clean)),
        "win_rate": float(np.mean(clean > 0.0)),
        "profit_factor": _profit_factor(clean),
        "gross_profit": positive_sum,
        "gross_loss": float(-clean[clean < 0.0].sum()),
        "best": float(clean.max()),
        "worst": float(clean.min()),
        "mean_without_best": None if without_best.size == 0 else float(without_best.mean()),
        "mean_without_worst": None if without_worst.size == 0 else float(without_worst.mean()),
        "top1_positive_share": None if positive_sum <= 0.0 else float(positive[:1].sum() / positive_sum),
        "top2_positive_share": None if positive_sum <= 0.0 else float(positive[:2].sum() / positive_sum),
    }


def _one_slot(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    choices = frame.sort_values(
        ["boundary", "abs_imbalance", "opening_notional_burst", "symbol"],
        ascending=[True, False, False, True], kind="stable",
    ).drop_duplicates("boundary", keep="first")
    selected: list[int] = []
    occupied_until: pd.Timestamp | None = None
    for idx, row in choices.sort_values("entry_time", kind="stable").iterrows():
        entry = pd.Timestamp(row["entry_time"])
        if occupied_until is not None and entry < occupied_until:
            continue
        selected.append(idx)
        occupied_until = entry + pd.Timedelta(minutes=horizon)
    return choices.loc[selected].copy()


def _bin_payload(frame: pd.DataFrame, horizon: int) -> dict[str, Any]:
    column = f"cont_{horizon}m"
    selected = _one_slot(frame, horizon)
    period_stats = {
        str(period): _stats(group[column])
        for period, group in frame.groupby("period_label", sort=True)
    }
    split_stats = {
        str(split): _stats(group[column])
        for split, group in frame.groupby("split", sort=True)
    }
    asset_stats = {
        str(asset): _stats(group[column])
        for asset, group in frame.groupby("symbol", sort=True)
    }
    slot_period = {
        str(period): _stats(group[column])
        for period, group in selected.groupby("period_label", sort=True)
    }
    slot_split = {
        str(split): _stats(group[column])
        for split, group in selected.groupby("split", sort=True)
    }
    slot_asset = {
        str(asset): _stats(group[column])
        for asset, group in selected.groupby("symbol", sort=True)
    }
    positive_periods = sum(1 for item in slot_period.values() if item.get("mean", 0.0) > 0.0)
    populated_periods = sum(1 for item in slot_period.values() if item.get("count", 0))
    slot = _stats(selected[column])
    reasons: list[str] = []
    if slot.get("mean", 0.0) <= 0.0 or (slot.get("profit_factor") or 0.0) <= 1.0:
        reasons.append("one-slot after-cost mean/PF is non-positive")
    if slot.get("mean_without_best") is not None and slot["mean_without_best"] <= 0.0:
        reasons.append("best-event removal eliminates the mean")
    post = slot_split.get("post_publication", {})
    if post.get("count", 0) and post.get("mean", 0.0) <= 0.0:
        reasons.append("post-publication one-slot mean is non-positive")
    if populated_periods and positive_periods < populated_periods:
        reasons.append(f"period signs are unstable ({positive_periods}/{populated_periods} positive)")
    if (slot.get("top1_positive_share") or 0.0) > 0.50:
        reasons.append("one event supplies over half of one-slot gross profit")
    return {
        "all_events": _stats(frame[column]),
        "by_period": period_stats,
        "by_split": split_stats,
        "by_asset": asset_stats,
        "one_slot": {
            **slot,
            "trades": int(len(selected)),
            "trades_per_day": float(len(selected) / EVALUATION_DAYS),
            "by_period": slot_period,
            "by_split": slot_split,
            "by_asset": slot_asset,
        },
        "status": "earns_untouched_confirmation" if not reasons else "do_not_promote",
        "reasons": reasons,
    }


def run(args: argparse.Namespace) -> None:
    candidate = Path(args.candidate_dir)
    events = pd.read_csv(candidate / "evidence" / "quarter-hour-v58" / "EVENTS.csv")
    for column in ("boundary", "entry_time"):
        events[column] = pd.to_datetime(events[column], utc=True, format="mixed", errors="raise")
    for column in ("same_phase_aligned", "top_of_hour", "funding_boundary"):
        events[column] = events[column].astype(str).str.lower().eq("true")

    bins: dict[str, Any] = {}
    for label in BINS:
        frame = events[events["abs_imbalance_bin"].eq(label)].copy()
        bins[label] = {
            "events": int(len(frame)),
            "horizons": {str(h): _bin_payload(frame, h) for h in HORIZONS},
        }

    moderate = events[events["abs_imbalance_bin"].eq("050_075")].copy()
    mechanism_groups: dict[str, Any] = {}
    for prefix, column in (
        ("same_phase_aligned", "same_phase_aligned"),
        ("burst", "notional_burst_bin"),
        ("phase", "phase_minute"),
        ("breadth", "cross_asset_breadth"),
        ("top_of_hour", "top_of_hour"),
        ("funding_boundary", "funding_boundary"),
    ):
        for key, group in moderate.groupby(column, sort=True, dropna=False):
            name = f"{prefix}:{key}"
            mechanism_groups[name] = {
                str(h): _stats(group[f"cont_{h}m"]) for h in HORIZONS
            }

    qualifying = [
        {"bin": label, "horizon_min": horizon}
        for label, payload in bins.items()
        for horizon, item in payload["horizons"].items()
        if item["status"] == "earns_untouched_confirmation"
    ]
    moderate_qualifying = [item for item in qualifying if item["bin"] == "050_075"]
    decision = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_experiment": "quarter-hour-v58",
        "evaluation_days": EVALUATION_DAYS,
        "frozen_bins": list(BINS),
        "frozen_horizons": list(HORIZONS),
        "bins": bins,
        "moderate_mechanism_groups": mechanism_groups,
        "qualifying_for_new_untouched_confirmation": qualifying,
        "decision": (
            "test_moderate_bin_on_new_untouched_periods"
            if moderate_qualifying
            else "discard_quarter_hour_family"
        ),
        "contract": (
            "The 0.50--0.75 interval was fixed before the v58 results as a descriptive bin. "
            "This audit can authorize a new untouched confirmation, but it cannot convert the "
            "same nine observed days into validation or tune a narrower threshold."
        ),
    }

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "DECISION.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    rows: list[dict[str, Any]] = []
    for label, payload in bins.items():
        for horizon, item in payload["horizons"].items():
            row = {
                "bin": label, "horizon_min": int(horizon), "status": item["status"],
                "reasons": " | ".join(item["reasons"]),
            }
            for prefix, stats in (("all", item["all_events"]), ("slot", item["one_slot"])):
                for key in ("count", "mean", "median", "win_rate", "profit_factor", "mean_without_best", "top1_positive_share"):
                    row[f"{prefix}_{key}"] = stats.get(key)
            row["slot_trades_per_day"] = item["one_slot"]["trades_per_day"]
            rows.append(row)
    pd.DataFrame(rows).to_csv(output / "BIN_DECISIONS.csv", index=False)

    md = [
        "# Quarter-hour fixed-bin decision audit", "",
        f"- decision: **{decision['decision']}**", f"- evaluation days: {EVALUATION_DAYS}",
        "- no threshold or horizon was changed after observing v58", "",
        "| absolute imbalance bin | horizon | one-slot trades | trades/day | mean bp | median bp | PF | mean without best bp | status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        pf = row.get("slot_profit_factor")
        lines = (
            f"| {row['bin']} | {row['horizon_min']}m | {int(row.get('slot_count') or 0)} | "
            f"{row['slot_trades_per_day']:.3f} | {10000*(row.get('slot_mean') or 0):.2f} | "
            f"{10000*(row.get('slot_median') or 0):.2f} | {'na' if pd.isna(pf) else f'{pf:.2f}'} | "
            f"{10000*(row.get('slot_mean_without_best') or 0):.2f} | {row['status']} |"
        )
        md.append(lines)
    md += ["", "A pooled subgroup is not promoted unless the same frozen subgroup remains positive after global one-slot routing, in every observed period including post-publication, and after removing its best event. Any authorized next test must use genuinely untouched dates.", ""]
    (output / "DECISION.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"decision": decision["decision"], "qualifying": qualifying}, indent=2))


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
