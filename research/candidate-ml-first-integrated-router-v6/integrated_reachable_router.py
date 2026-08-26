#!/usr/bin/env python3
"""Fuse learned reachability and deterministic auction families, then evaluate new windows.

All earlier eight windows become development observations.  Eight newly harvested
windows are identified only by their `h-` period prefix and remain untouched
until the integrated component union is fixed.  Components compete through one
global pending-order/position timeline, so their trades are never added after the
fact.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


def import_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RESEARCH = Path(__file__).resolve().parents[1]
CORE = import_module(
    "reachable_control_v4_core",
    RESEARCH / "candidate-ml-first-reachable-control-v4" / "reachable_control_policy.py",
)
FAMILY = import_module(
    "causal_family_v5_core",
    RESEARCH / "candidate-ml-first-causal-family-router-v5" / "causal_family_router_fixed.py",
)


@dataclass(frozen=True)
class IntegratedComponent:
    source: str
    mechanism: str
    fractions: tuple[float, ...]
    threshold: float
    score: float

    @property
    def name(self) -> str:
        fractions = "+".join(f"{value:.2f}" for value in self.fractions)
        return f"{self.source}|{self.mechanism}|f={fractions}|cut={self.threshold:.6g}"


def attach_roles(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    holdout = result["_period"].astype(str).str.lower().str.contains(r"(^|/)h-", regex=True)
    result["_role"] = np.where(holdout, "fresh", "dev")
    return result


def integrated_mask(frame: pd.DataFrame, component: IntegratedComponent) -> pd.Series:
    mechanism_mask = frame["_mechanism"].eq(component.mechanism) if component.mechanism != "all" else pd.Series(True, index=frame.index)
    fraction_mask = frame["route_fraction"].round(6).isin([round(value, 6) for value in component.fractions])
    score_column = "_hazard_score" if component.source == "hazard" else "_family_score"
    return mechanism_mask & fraction_mask & (frame[score_column] >= component.threshold)


def candidate_union(frame: pd.DataFrame, components: list[IntegratedComponent]) -> pd.DataFrame:
    pieces = []
    for rank, component in enumerate(components):
        subset = frame[integrated_mask(frame, component)].copy()
        subset["_component"] = component.name
        subset["_component_priority"] = component.score + 1e-9 * (len(components) - rank)
        subset["_routing_score"] = subset["_hazard_score"] if component.source == "hazard" else subset["_family_score"]
        pieces.append(subset)
    if not pieces:
        return frame.iloc[0:0].copy()
    candidates = pd.concat(pieces, ignore_index=True, sort=False)
    candidates = candidates.sort_values(
        ["_period", "_episode", "_decision", "_component_priority", "_routing_score", "route_fraction"],
        ascending=[True, True, True, False, False, True],
        kind="mergesort",
    )
    return candidates.drop_duplicates(["_period", "_episode", "_decision"], keep="first")


def simulate(frame: pd.DataFrame, components: list[IntegratedComponent], periods: list[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    candidates = candidate_union(frame, components)
    completed_rows = []
    selected_rows = []
    nav = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    period_rows = []
    for period in periods:
        subset = candidates[candidates["_period"] == period].copy()
        subset["_minute"] = subset["_decision"].dt.floor("min")
        busy_until = pd.Timestamp.min.tz_localize("UTC")
        locked: set[str] = set()
        before = nav
        period_completed = 0
        for _, simultaneous in subset.groupby("_minute", sort=True):
            decision = simultaneous["_decision"].min()
            if decision < busy_until:
                continue
            simultaneous = simultaneous[~simultaneous["_episode"].isin(locked)]
            if simultaneous.empty:
                continue
            chosen = simultaneous.sort_values(
                ["_component_priority", "_routing_score", "route_fraction"],
                ascending=[False, False, True],
                kind="mergesort",
            ).iloc[0]
            locked.add(str(chosen["_episode"]))
            selected_rows.append(chosen)
            if bool(chosen["_filled"]):
                busy_until = max(chosen["_exit_ts"], chosen["_fill_ts"] + pd.Timedelta(minutes=1))
                if pd.notna(chosen["_net_r"]):
                    value = float(chosen["_net_r"])
                    nav *= max(1e-12, 1.0 + CORE.RISK * value)
                    peak = max(peak, nav)
                    maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
                    completed_rows.append(chosen)
                    period_completed += 1
            else:
                busy_until = max(chosen["_cancel_ts"], decision + pd.Timedelta(minutes=1))
        period_rows.append({
            "period": period,
            "completed_trades": period_completed,
            "nav_multiplier": float(nav / before) if before else 0.0,
            "log_growth": float(math.log(max(1e-12, nav / before))) if before else -math.inf,
        })

    completed = pd.DataFrame(completed_rows) if completed_rows else frame.iloc[0:0].copy()
    selected = pd.DataFrame(selected_rows) if selected_rows else frame.iloc[0:0].copy()
    values = completed["_net_r"].astype(float).to_numpy() if len(completed) else np.array([], dtype=float)
    gross_win = float(values[values > 0].sum()) if len(values) else 0.0
    gross_loss = float(-values[values < 0].sum()) if len(values) else 0.0
    logs = np.array([row["log_growth"] for row in period_rows], dtype=float)
    days = max(1, 7 * len(periods))
    trades_per_day = len(completed) / days
    robust = math.log(max(1e-12, nav)) - 0.55 * float(logs.std(ddof=0) * math.sqrt(max(1, len(logs)))) - 0.04 * max(0.0, 1.0 - trades_per_day)
    if len(completed) == 0:
        robust = -1e9
    stats = {
        "selected_plans": int(len(selected)),
        "completed_trades": int(len(completed)),
        "calendar_days": int(days),
        "trades_per_day": float(trades_per_day),
        "win_rate": float((values > 0).mean()) if len(values) else 0.0,
        "mean_net_r": float(values.mean()) if len(values) else 0.0,
        "median_net_r": float(np.median(values)) if len(values) else 0.0,
        "profit_factor_r": float(gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else math.inf),
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "robust_objective": float(robust),
        "by_period": period_rows,
    }
    return completed, stats


def convert_components(source: str, components) -> list[IntegratedComponent]:
    return [
        IntegratedComponent(
            source=source,
            mechanism=str(component.mechanism),
            fractions=tuple(float(value) for value in component.fractions),
            threshold=float(component.threshold),
            score=float(component.score),
        )
        for component in components
    ]


def greedy(frame: pd.DataFrame, candidates: list[IntegratedComponent], dev_periods: list[str]) -> list[IntegratedComponent]:
    dev = frame[frame["_role"] == "dev"]
    selected: list[IntegratedComponent] = []
    current = -1e18
    for _ in range(7):
        best = None
        best_score = current
        used = {(component.source, component.mechanism) for component in selected}
        for candidate in candidates[:260]:
            if (candidate.source, candidate.mechanism) in used:
                continue
            _, stats = simulate(dev, selected + [candidate], dev_periods)
            if stats["robust_objective"] > best_score + 0.001:
                best = candidate
                best_score = float(stats["robust_objective"])
        if best is None:
            break
        selected.append(best)
        current = best_score
    return selected


def safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, tuple):
        return [safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    raw = CORE.read_plans(args.root)
    frame, schema = CORE.normalize(raw)
    frame = attach_roles(frame)
    numeric, categorical = CORE.feature_columns(frame)
    predicted, model_bundle = CORE.predict_hazards(frame, numeric, categorical)
    predicted = attach_roles(predicted)
    predicted["_mechanism"] = CORE.mechanism(predicted)
    predicted["_hazard_score"] = predicted["_expected_log"]
    scored, family_model = FAMILY.attach_scores(predicted)
    scored = attach_roles(scored)
    scored["_hazard_score"] = predicted["_hazard_score"]
    scored["_family_score"] = scored["_causal_score"]

    dev_periods = sorted(scored.loc[scored["_role"] == "dev", "_period"].unique())
    fresh_periods = sorted(scored.loc[scored["_role"] == "fresh", "_period"].unique())
    if len(dev_periods) < 6 or len(fresh_periods) < 6:
        raise SystemExit(f"unexpected period partition: dev={dev_periods}, fresh={fresh_periods}")

    hazard_components, hazard_catalog = CORE.search_components(scored[scored["_role"] == "dev"], dev_periods)
    family_components, family_catalog = FAMILY.search(scored, dev_periods)
    hazard = convert_components("hazard", hazard_components)
    deterministic = convert_components("family", family_components)
    candidates = sorted(hazard + deterministic, key=lambda component: component.score, reverse=True)
    if not candidates:
        raise SystemExit("no integrated component candidates")

    policies: dict[str, list[IntegratedComponent]] = {}
    if hazard:
        policies["best_hazard_component"] = [hazard[0]]
    if deterministic:
        policies["best_family_component"] = [deterministic[0]]
    fused = greedy(scored, candidates, dev_periods)
    if fused:
        policies["integrated_reachable_router"] = fused

    rows = []
    completed_by_name = {}
    stats_by_name = {}
    for name, policy in policies.items():
        dev_completed, dev_stats = simulate(scored[scored["_role"] == "dev"], policy, dev_periods)
        fresh_completed, fresh_stats = simulate(scored[scored["_role"] == "fresh"], policy, fresh_periods)
        completed_by_name[name] = pd.concat([
            dev_completed.assign(_evaluation_role="development"),
            fresh_completed.assign(_evaluation_role="fresh"),
        ], ignore_index=True, sort=False)
        stats_by_name[name] = (dev_stats, fresh_stats)
        rows.append({
            "variant": name,
            "development_objective": dev_stats["robust_objective"],
            "development_trades": dev_stats["completed_trades"],
            "development_mean_net_r": dev_stats["mean_net_r"],
            "development_nav": dev_stats["ending_nav_multiplier"],
            "development_maximum_drawdown": dev_stats["maximum_drawdown"],
            "fresh_trades": fresh_stats["completed_trades"],
            "fresh_mean_net_r": fresh_stats["mean_net_r"],
            "fresh_nav": fresh_stats["ending_nav_multiplier"],
            "fresh_maximum_drawdown": fresh_stats["maximum_drawdown"],
            "fresh_trades_per_day": fresh_stats["trades_per_day"],
            "components": len(policy),
        })
    variants = pd.DataFrame(rows).sort_values(["development_objective", "development_trades"], ascending=[False, False])
    selected_name = str(variants.iloc[0]["variant"])
    selected_policy = policies[selected_name]
    development, fresh_stats = stats_by_name[selected_name]
    completed = completed_by_name[selected_name]

    summary = {
        "policy": "ML_FIRST_INTEGRATED_REACHABLE_ROUTER_V6",
        "selected_variant": selected_name,
        "components": [{
            "name": component.name,
            "source": component.source,
            "mechanism": component.mechanism,
            "fractions": list(component.fractions),
            "threshold": component.threshold,
            "development_component_score": component.score,
        } for component in selected_policy],
        "development": development,
        "fresh": fresh_stats,
        "development_periods": dev_periods,
        "fresh_periods": fresh_periods,
        "numeric_features": numeric,
        "categorical_features": categorical,
        "schema": schema,
        "family_score_model": family_model,
        "account_contract": {
            "risk_fraction": CORE.RISK,
            "one_global_pending_or_position": True,
            "causal_episode_lockout": True,
            "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "entry_stop_target_fixed_before_selection": True,
        },
    }

    output_columns = [column for column in completed.columns if not str(column).startswith("_")]
    output_columns += ["_period", "_evaluation_role", "_decision", "_fill_ts", "_exit_ts", "_net_r", "_hazard_score", "_family_score", "_mechanism", "_component"]
    output_columns = list(dict.fromkeys(column for column in output_columns if column in completed.columns))
    completed[output_columns].to_csv(args.output / "completed_trades.csv", index=False)
    variants.to_csv(args.output / "variant_metrics.csv", index=False)
    hazard_catalog.assign(source="hazard").to_csv(args.output / "hazard_component_catalog.csv", index=False)
    family_catalog.assign(source="family").to_csv(args.output / "family_component_catalog.csv", index=False)
    pd.DataFrame(development["by_period"] + fresh_stats["by_period"]).to_csv(args.output / "period_metrics.csv", index=False)
    (args.output / "summary.json").write_text(json.dumps(safe(summary), indent=2, sort_keys=True) + "\n")
    joblib.dump(model_bundle | {"selected_policy": selected_policy, "family_score_model": family_model}, args.output / "model_bundle.joblib")
    print(json.dumps(safe(summary), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
