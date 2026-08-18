#!/usr/bin/env python3
"""Trade-level clinic for integrated generator, model and account behavior.

This is a research instrument, not a pass/fail framework.  It separates missing
opportunities, wrong direction, late entry, fragile invalidation, unrealistic
objectives and account-slot conflicts so the next market-logic change addresses
the dominant failure rather than adding another arbitrary threshold.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--training-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _distribution(values: pd.Series) -> dict[str, Any]:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return {"n": 0}
    return {
        "n": int(len(numeric)),
        "mean": float(numeric.mean()),
        "median": float(numeric.median()),
        "p10": float(numeric.quantile(0.10)),
        "p25": float(numeric.quantile(0.25)),
        "p75": float(numeric.quantile(0.75)),
        "p90": float(numeric.quantile(0.90)),
        "min": float(numeric.min()),
        "max": float(numeric.max()),
    }


def _outcomes(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0}
    result: dict[str, Any] = {"rows": int(len(frame))}
    if "label" in frame:
        result["target_first_rate"] = float(pd.to_numeric(frame["label"]).mean())
    for name in (
        "counterfactual_net_r_conservative",
        "counterfactual_minutes_to_resolution",
        "gross_rr",
        "target_account_r",
        "crossfit_probability",
        "expected_log_growth",
        "expected_log_growth_per_hour",
        "integrated_structure_60_side",
        "integrated_structure_15_side",
        "integrated_structure_min_side",
        "integrated_structure_disagreement",
        "integrated_channel_confluence",
        "integrated_noise_to_risk_log",
    ):
        column = name if name in frame else f"mlf_{name}"
        if column in frame:
            result[name] = _distribution(frame[column])
    return result


def _feature_bins(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "label" not in frame:
        return rows
    candidates = [
        name
        for name in frame.columns
        if name.startswith("mlf_integrated_")
        or name
        in {
            "gross_rr",
            "risk_bps",
            "target_bps",
            "counterfactual_minutes_to_resolution",
            "crossfit_probability",
            "target_account_r",
        }
    ]
    for name in candidates:
        values = pd.to_numeric(frame[name], errors="coerce")
        if values.notna().sum() < 30 or values.nunique() < 4:
            continue
        try:
            buckets = pd.qcut(values, q=min(6, values.nunique()), duplicates="drop")
        except Exception:
            continue
        table = pd.DataFrame(
            {
                "bucket": buckets,
                "label": pd.to_numeric(frame["label"], errors="coerce"),
                "net_r": pd.to_numeric(
                    frame.get("counterfactual_net_r_conservative"), errors="coerce"
                ),
                "accepted": frame.get("accepted", False),
            }
        )
        for bucket, group in table.groupby("bucket", observed=True):
            rows.append(
                {
                    "feature": name,
                    "bucket": str(bucket),
                    "n": int(len(group)),
                    "target_first_rate": float(group["label"].mean()),
                    "mean_net_r": float(group["net_r"].mean()),
                    "accepted_fraction": float(
                        pd.Series(group["accepted"]).astype(bool).mean()
                    ),
                }
            )
    return rows


def _mfe_columns(frame: pd.DataFrame) -> list[str]:
    result = []
    for name in frame.columns:
        lower = name.lower()
        if "mfe" in lower or "maximum_favorable" in lower:
            if pd.to_numeric(frame[name], errors="coerce").notna().any():
                result.append(name)
    return result


def _failure_clinic(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if frame.empty or "label" not in frame:
        return pd.DataFrame(), {"rows": 0}
    losses = frame[pd.to_numeric(frame["label"], errors="coerce").eq(0)].copy()
    if losses.empty:
        return losses, {"rows": 0}
    mfe_names = _mfe_columns(losses)
    mfe = pd.Series(np.nan, index=losses.index, dtype=float)
    for name in mfe_names:
        mfe = pd.concat(
            [mfe, pd.to_numeric(losses[name], errors="coerce")], axis=1
        ).max(axis=1)
    losses["clinic_best_mfe_r"] = mfe
    structure_min = pd.to_numeric(
        losses.get("mlf_integrated_structure_min_side"), errors="coerce"
    )
    structure_disagreement = pd.to_numeric(
        losses.get("mlf_integrated_structure_disagreement"), errors="coerce"
    )
    channel = pd.to_numeric(
        losses.get("mlf_integrated_channel_confluence"), errors="coerce"
    )
    noise_log = pd.to_numeric(
        losses.get("mlf_integrated_noise_to_risk_log"), errors="coerce"
    )
    losses["clinic_failure_mode"] = "UNRESOLVED_DIRECTION_OR_EVENT"
    losses.loc[structure_min < -0.35, "clinic_failure_mode"] = "STRUCTURE_OPPOSED_DIRECTION"
    losses.loc[structure_disagreement > 0.70, "clinic_failure_mode"] = "HIGHER_LOWER_STRUCTURE_CONFLICT"
    losses.loc[noise_log > -0.30, "clinic_failure_mode"] = "STOP_NOT_WIDE_BEYOND_CAUSAL_NOISE"
    losses.loc[mfe >= 0.80, "clinic_failure_mode"] = "DIRECTION_WORKED_OBJECTIVE_OR_EXIT_FAILED"
    losses.loc[
        (mfe >= 0.35) & (mfe < 0.80), "clinic_failure_mode"
    ] = "PARTIAL_EDGE_BUT_ENTRY_OR_OBJECTIVE_FRAGILE"
    losses.loc[
        (channel < 0.05) & (structure_min < 0.10) & (mfe < 0.35),
        "clinic_failure_mode",
    ] = "WEAK_LOCATION_AND_WEAK_DIRECTION"
    counts = losses["clinic_failure_mode"].value_counts()
    summary = {
        "rows": int(len(losses)),
        "mfe_columns": mfe_names,
        "best_mfe_r": _distribution(mfe),
        "modes": {str(key): int(value) for key, value in counts.items()},
        "mode_fraction": {
            str(key): float(value / len(losses)) for key, value in counts.items()
        },
    }
    return losses, summary


def _period_account(training_summary: dict[str, Any]) -> dict[str, Any]:
    crossfit = dict(training_summary.get("crossfit", {}))
    result: dict[str, Any] = {}
    for period, record in crossfit.items():
        if not isinstance(record, dict):
            continue
        account = record.get("account")
        if isinstance(account, dict):
            result[period] = account
    return result


def _holdout_metrics(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    holdouts = root / "holdouts"
    if not holdouts.exists():
        return result
    for directory in sorted(item for item in holdouts.iterdir() if item.is_dir()):
        record: dict[str, Any] = {}
        for name in ("metrics.json", "run.json"):
            path = directory / name
            if path.exists():
                record[name] = _read_json(path)
        events_path = directory / "decision_events.csv"
        if events_path.exists():
            events = pd.read_csv(events_path, low_memory=False)
            record["event_kinds"] = {
                str(key): int(value)
                for key, value in events.get("kind", pd.Series(dtype=str))
                .value_counts(dropna=False)
                .items()
            }
            if "kind" in events:
                scores = events[
                    events["kind"].astype(str).eq("integrated_plan_scored")
                ]
                record["scored_plans"] = int(len(scores))
                if not scores.empty:
                    record["accepted_plans"] = int(
                        scores.get("accepted", False).astype(bool).sum()
                    )
                    for column in (
                        "robust_target_probability",
                        "expected_account_r",
                        "expected_log_growth",
                        "expected_log_growth_per_hour",
                    ):
                        if column in scores:
                            record[column] = _distribution(scores[column])
        result[directory.name] = record
    return result


def _research_implications(
    period_accounts: dict[str, Any],
    failure: dict[str, Any],
    crossfit: pd.DataFrame,
    holdouts: dict[str, Any],
) -> list[str]:
    implications: list[str] = []
    accounts = [value for key, value in period_accounts.items() if key != "combined_crossfit"]
    trade_counts = [int(item.get("trades", 0)) for item in accounts]
    ending = [float(item.get("ending_nav", 1.0)) for item in accounts]
    if trade_counts and np.median(trade_counts) < 3:
        implications.append(
            "The coherent generator remains opportunity-poor; add an independent directional impulse/first-pullback family rather than loosening liquidity-event definitions."
        )
    if ending and sum(value > 1.0 for value in ending) < len(ending) / 2.0:
        implications.append(
            "Period instability remains upstream of account sizing; rework direction/control-state ownership before considering longer evaluation."
        )
    fractions = dict(failure.get("mode_fraction", {}))
    if fractions.get("DIRECTION_WORKED_OBJECTIVE_OR_EXIT_FAILED", 0.0) >= 0.25:
        implications.append(
            "A substantial share of losses first moved materially in favor; target the first decision-scale opposing auction and model continuation only after that objective, without partial exits."
        )
    if fractions.get("STRUCTURE_OPPOSED_DIRECTION", 0.0) + fractions.get(
        "HIGHER_LOWER_STRUCTURE_CONFLICT", 0.0
    ) >= 0.25:
        implications.append(
            "Directional context is not a stable scalar hierarchy; represent draw-on-liquidity, control transfer and trend persistence as separate latent states."
        )
    if fractions.get("STOP_NOT_WIDE_BEYOND_CAUSAL_NOISE", 0.0) >= 0.20:
        implications.append(
            "Event invalidation is still confused with ordinary path noise; derive the stop from the full causal episode extreme plus prior impact/noise, not the mitigation candle alone."
        )
    if not implications:
        implications.append(
            "The next information gain should come from longer continuous account paths and live-like execution differences, not another geometry threshold sweep."
        )
    return implications


def clinic(training_root: Path, output_path: Path) -> dict[str, Any]:
    model_root = training_root / "model"
    summary_path = model_root / "integrated_training_summary.json"
    crossfit_path = model_root / "integrated_crossfit_plans.csv"
    if not summary_path.exists() or not crossfit_path.exists():
        raise RuntimeError("training evidence is incomplete")
    training_summary = _read_json(summary_path)
    crossfit = pd.read_csv(crossfit_path, low_memory=False)
    period_accounts = _period_account(training_summary)
    losses, failure_summary = _failure_clinic(crossfit)
    losses_path = output_path.with_name("integrated_loss_clinic.csv")
    losses.to_csv(losses_path, index=False)
    feature_bins = _feature_bins(crossfit)
    feature_path = output_path.with_name("integrated_feature_outcomes.csv")
    pd.DataFrame(feature_bins).to_csv(feature_path, index=False)
    holdouts = _holdout_metrics(training_root)
    result = {
        "training_summary": str(summary_path),
        "crossfit_plans": int(len(crossfit)),
        "crossfit_outcomes": _outcomes(crossfit),
        "accepted_crossfit_outcomes": _outcomes(
            crossfit[crossfit.get("accepted", False).astype(bool)]
        ),
        "period_accounts": period_accounts,
        "failure_clinic": failure_summary,
        "loss_clinic_path": str(losses_path),
        "feature_outcomes_path": str(feature_path),
        "holdouts": holdouts,
        "research_implications": _research_implications(
            period_accounts, failure_summary, crossfit, holdouts
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    args = parse_args()
    output = args.output or args.training_root / "integrated_research_clinic.json"
    result = clinic(args.training_root, output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
