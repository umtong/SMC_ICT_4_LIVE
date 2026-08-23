#!/usr/bin/env python3
"""Candidate 4t v2: state-level ownership plus global slot commitment value."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import candidate_4t_policy as base

NS_PER_HOUR = base.NS_PER_HOUR
EPS = base.EPS


def canonical_states(frame: pd.DataFrame) -> pd.DataFrame:
    """One deterministic, geometry-neutral feature row per observable state."""
    keys = ["period", "episode_id", "state_id", "order_time_ns"]
    return (
        frame.sort_values(keys + ["action_id"])
        .drop_duplicates(keys, keep="first")
        .reset_index(drop=True)
    )


def ownership_training_states(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate duplicated action outcomes into one soft state ownership label."""
    keys = ["period", "episode_id", "state_id", "order_time_ns"]
    resolved = frame[
        frame.filled & frame.resolved & frame.net_r.notna()
    ].copy()
    if resolved.empty:
        return frame.iloc[:0].copy()
    labels = resolved.groupby(keys, as_index=False).agg(
        ownership_target=("win", "mean"),
        ownership_observations=("win", "size"),
    )
    states = canonical_states(frame)
    return states.merge(labels, on=keys, how="inner", validate="one_to_one")


def predict_ownership(
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, float, dict[str, Any]]:
    train_states = ownership_training_states(train)
    test_states = canonical_states(test)
    if train_states.empty:
        raw = np.full(len(test_states), 0.5)
        std = np.zeros(len(test_states))
        prior = 0.5
        feature_manifest: dict[str, Any] = {"global": [], "families": {}}
    else:
        raw, std, prior, names = base.ensemble_probability(
            train_states, test_states, "ownership_target", "ownership"
        )
        feature_manifest = {"global": names, "families": {}}
        family_values = test_states.get(
            "family", pd.Series("UNKNOWN", index=test_states.index)
        ).fillna("UNKNOWN").astype(str)
        train_families = train_states.get(
            "family", pd.Series("UNKNOWN", index=train_states.index)
        ).fillna("UNKNOWN").astype(str)
        for family in sorted(family_values.unique()):
            train_mask = train_families.eq(family)
            test_mask = family_values.eq(family)
            family_train = train_states.loc[train_mask]
            target = pd.to_numeric(
                family_train.get("ownership_target"), errors="coerce"
            ).dropna()
            if len(family_train) < 160 or target.nunique() < 2:
                continue
            fam_mean, fam_std, fam_prior, fam_names = base.ensemble_probability(
                train_states,
                test_states.loc[test_mask],
                "ownership_target",
                "ownership",
                train_mask,
            )
            blend = min(0.82, len(family_train) / (len(family_train) + 260.0))
            raw[test_mask.to_numpy()] = (
                (1.0 - blend) * raw[test_mask.to_numpy()] + blend * fam_mean
            )
            std[test_mask.to_numpy()] = np.sqrt(
                (1.0 - blend) * std[test_mask.to_numpy()] ** 2
                + blend * fam_std**2
                + blend * (1.0 - blend)
                * (fam_mean - raw[test_mask.to_numpy()]) ** 2
            )
            feature_manifest["families"][family] = {
                "states": int(len(family_train)),
                "prior": float(fam_prior),
                "blend": float(blend),
                "features": fam_names,
            }
    state_prediction = test_states[["period", "state_id"]].copy()
    state_prediction["p_ownership_raw_mean"] = raw
    state_prediction["p_ownership_raw_std"] = std
    state_prediction["ownership_prior_v2"] = prior
    merged = test[["period", "state_id"]].merge(
        state_prediction,
        on=["period", "state_id"],
        how="left",
        validate="many_to_one",
    )
    return (
        merged.p_ownership_raw_mean.to_numpy(float),
        merged.p_ownership_raw_std.to_numpy(float),
        float(prior),
        feature_manifest,
    )


def score_train_test(
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    train = train.copy()
    train["resolved_after_fill"] = np.where(
        train.filled, train.resolved.astype(float), np.nan
    )
    own_mean, own_std, own_prior, own_manifest = predict_ownership(train, test)
    fill_mean, fill_std, fill_prior, fill_features = base.ensemble_probability(
        train, test, "filled", "execution"
    )
    resolve_mean, resolve_std, resolve_prior, resolve_features = (
        base.ensemble_probability(
            train,
            test,
            "resolved_after_fill",
            "execution",
            train.filled,
        )
    )
    duration_train = train[np.isfinite(train.terminal_minutes_label)].copy()
    if len(duration_train) >= 90:
        duration_model = base.fit_ridge(
            duration_train,
            np.log1p(duration_train.terminal_minutes_label.to_numpy(float)),
            base.decision_weights(duration_train),
            "execution",
            24.0,
        )
        duration = np.expm1(duration_model.predict(test))
        duration_features = duration_model.encoder.names
    else:
        median = (
            float(duration_train.terminal_minutes_label.median())
            if len(duration_train)
            else 60.0
        )
        duration = np.full(len(test), median)
        duration_features = []
    output = test.copy()
    output["p_ownership_raw"] = base.reliability_shrink(
        own_mean, own_std, own_prior
    )
    output["p_ownership_model_std"] = own_std
    output["p_fill"] = base.reliability_shrink(
        fill_mean, fill_std, fill_prior
    )
    output["p_resolve"] = base.reliability_shrink(
        resolve_mean, resolve_std, resolve_prior
    )
    output["predicted_terminal_minutes"] = np.maximum(1.0, duration)
    output["ownership_prior"] = own_prior
    manifest = {
        "ownership_prior": own_prior,
        "fill_prior": fill_prior,
        "resolve_prior": resolve_prior,
        "ownership_state_model": own_manifest,
        "fill_features": fill_features,
        "resolve_features": resolve_features,
        "duration_features": duration_features,
    }
    return output, manifest


def state_soft_targets(frame: pd.DataFrame) -> pd.DataFrame:
    states = ownership_training_states(frame)
    if states.empty:
        return states
    keys = ["period", "episode_id", "state_id", "order_time_ns"]
    raw = frame.groupby(keys, as_index=False).agg(
        raw=("p_ownership_raw", "mean"),
        prior=("ownership_prior", "mean"),
        phase=("auction_phase", "first"),
    )
    if "auction_progress_r" in frame:
        progress = frame.groupby(keys, as_index=False).agg(
            progress=("auction_progress_r", "mean")
        )
    else:
        progress = raw[keys].copy()
        progress["progress"] = 0.0
    if "auction_failure_pressure" in frame:
        failure = frame.groupby(keys, as_index=False).agg(
            failure=("auction_failure_pressure", "mean")
        )
    else:
        failure = raw[keys].copy()
        failure["failure"] = 0.0
    output = states[keys + ["ownership_target"]].rename(
        columns={"ownership_target": "target"}
    )
    output = output.merge(raw, on=keys, how="left", validate="one_to_one")
    output = output.merge(progress, on=keys, how="left", validate="one_to_one")
    output = output.merge(failure, on=keys, how="left", validate="one_to_one")
    return output.sort_values(keys).reset_index(drop=True)


def global_reservation_targets(states: pd.DataFrame) -> pd.DataFrame:
    """Best independent opportunity blocked by an immutable filled position."""
    output = states.copy()
    output["global_reservation_target"] = 0.0
    for _, period in output.groupby("period", sort=False):
        ordered = period.sort_values(["order_time_ns", "state_id"])
        indices = list(ordered.index)
        times = ordered.order_time_ns.to_numpy(float)
        episodes = ordered.episode_id.astype(str).to_numpy()
        values = np.maximum(0.0, ordered.expected_enter_log.to_numpy(float))
        terminals = pd.to_numeric(
            ordered.get("terminal_ns", ordered.order_time_ns), errors="coerce"
        ).to_numpy(float)
        predicted_end = times + (
            np.maximum(
                1.0,
                pd.to_numeric(
                    ordered.predicted_terminal_minutes, errors="coerce"
                ).fillna(60.0).to_numpy(float),
            )
            * base.NS_PER_MINUTE
        )
        terminals = np.where(
            np.isfinite(terminals) & (terminals > times), terminals, predicted_end
        )
        for left, row_index in enumerate(indices):
            best = 0.0
            for right in range(left + 1, len(indices)):
                if times[right] > terminals[left]:
                    break
                if episodes[right] == episodes[left]:
                    continue
                delay_hours = max(0.0, (times[right] - times[left]) / NS_PER_HOUR)
                value = values[right] * math.exp(-0.035 * delay_hours)
                if value > best:
                    best = value
            output.loc[row_index, "global_reservation_target"] = best
    return output


def attach_global_reservation(
    training_states: pd.DataFrame,
    test_states: pd.DataFrame,
) -> pd.DataFrame:
    output = test_states.copy()
    if len(training_states) < 80:
        output["expected_global_reservation"] = 0.0
    else:
        model = base.fit_ridge(
            training_states,
            training_states.global_reservation_target.to_numpy(float),
            base.decision_weights(training_states),
            "continuation",
            32.0,
        )
        output["expected_global_reservation"] = np.maximum(
            0.0, model.predict(output)
        )
    opportunity_cost = (
        output.p_fill.to_numpy(float)
        * output.expected_global_reservation.to_numpy(float)
    )
    output["same_episode_wait_log"] = pd.to_numeric(
        output.get("expected_wait_log", 0.0), errors="coerce"
    ).fillna(0.0)
    output["global_commitment_cost"] = opportunity_cost
    output["expected_wait_log"] = np.maximum(
        output.same_episode_wait_log.to_numpy(float), opportunity_cost
    )
    output["stopping_advantage"] = (
        output.expected_enter_log.to_numpy(float)
        - output.expected_wait_log.to_numpy(float)
    )
    return output


def diagnostics(
    states: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    losses, missed = base.diagnostics(states, orders, trades)
    additions = [
        "same_episode_wait_log",
        "expected_global_reservation",
        "global_commitment_cost",
    ]
    for target, source in ((losses, trades), (missed, states)):
        for column in additions:
            if column in source and column not in target:
                lookup = source[["period", "action_id", column]].drop_duplicates(
                    ["period", "action_id"]
                )
                target = target.merge(
                    lookup,
                    on=["period", "action_id"],
                    how="left",
                    validate="one_to_one",
                )
        if target is losses:
            losses = target
        else:
            missed = target
    return losses, missed


def run(
    development_root: Path,
    fresh_root: Path | None,
    output: Path,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    development = base.load_actions(development_root)
    periods = sorted(development.period.unique())
    if len(periods) < 2:
        raise ValueError("candidate 4t v2 requires separated development periods")

    oof_parts: list[pd.DataFrame] = []
    manifests: dict[str, Any] = {}
    for period in periods:
        scored, manifest = score_train_test(
            development[development.period != period],
            development[development.period == period],
        )
        oof_parts.append(scored)
        manifests[f"development_holdout:{period}"] = manifest
    development_scored = pd.concat(oof_parts, ignore_index=True, sort=False)
    filter_training = state_soft_targets(development_scored)

    filtered_parts: list[pd.DataFrame] = []
    filter_parameters: dict[str, Any] = {}
    for period in periods:
        part, parameters = base.attach_filtered_ownership(
            development_scored[development_scored.period == period].copy(),
            filter_training[filter_training.period != period],
        )
        filtered_parts.append(part)
        filter_parameters[f"development_holdout:{period}"] = {
            "decay": parameters[0],
            "evidence_weight": parameters[1],
        }
    development_scored = base.attach_enter_values(
        pd.concat(filtered_parts, ignore_index=True, sort=False)
    )
    development_state_base = base.state_best(development_scored)
    development_state_targets = global_reservation_targets(
        base.continuation_targets(development_state_base)
    )

    state_parts: list[pd.DataFrame] = []
    for period in periods:
        training = development_state_targets[
            development_state_targets.period != period
        ]
        held_out = development_state_targets[
            development_state_targets.period == period
        ]
        held_out = base.attach_continuation(training, held_out)
        held_out = attach_global_reservation(training, held_out)
        state_parts.append(held_out)
    development_states = pd.concat(state_parts, ignore_index=True, sort=False)
    dev_orders, dev_trades, dev_replacements, dev_summary = base.route(
        development_states
    )
    dev_losses, dev_missed = diagnostics(
        development_states, dev_orders, dev_trades
    )

    result: dict[str, Any] = {
        "policy": "CANDIDATE_4T_V2_STATE_OWNERSHIP_GLOBAL_COMMITMENT_VALUE",
        "development_oof": dev_summary,
        "filter_parameters": filter_parameters,
        "manifests": manifests,
        "input_hashes": {"development": base.input_hashes(development_root)},
    }
    development_scored.to_csv(
        output / "development_action_scores.csv", index=False
    )
    development_states.to_csv(output / "development_states.csv", index=False)
    dev_orders.to_csv(output / "development_orders.csv", index=False)
    dev_trades.to_csv(output / "development_trades.csv", index=False)
    dev_replacements.to_csv(
        output / "development_replacements.csv", index=False
    )
    dev_losses.to_csv(output / "development_loss_clinic.csv", index=False)
    dev_missed.to_csv(
        output / "development_missed_opportunity_clinic.csv", index=False
    )

    if fresh_root is not None:
        fresh = base.load_actions(fresh_root)
        fresh_scored, manifest = score_train_test(development, fresh)
        manifests["fresh"] = manifest
        fresh_scored, parameters = base.attach_filtered_ownership(
            fresh_scored, filter_training
        )
        filter_parameters["fresh"] = {
            "decay": parameters[0],
            "evidence_weight": parameters[1],
        }
        fresh_scored = base.attach_enter_values(fresh_scored)
        fresh_state_base = base.state_best(fresh_scored)
        fresh_state_targets = base.continuation_targets(fresh_state_base)
        fresh_states = base.attach_continuation(
            development_state_targets, fresh_state_targets
        )
        fresh_states = attach_global_reservation(
            development_state_targets, fresh_states
        )
        orders, trades, replacements, summary = base.route(fresh_states)
        losses, missed = diagnostics(fresh_states, orders, trades)
        fresh_scored.to_csv(output / "fresh_action_scores.csv", index=False)
        fresh_states.to_csv(output / "fresh_states.csv", index=False)
        orders.to_csv(output / "fresh_orders.csv", index=False)
        trades.to_csv(output / "fresh_trades.csv", index=False)
        replacements.to_csv(output / "fresh_replacements.csv", index=False)
        losses.to_csv(output / "fresh_loss_clinic.csv", index=False)
        missed.to_csv(
            output / "fresh_missed_opportunity_clinic.csv", index=False
        )
        result["fresh"] = summary
        result["input_hashes"]["fresh"] = base.input_hashes(fresh_root)

    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output / "model_manifest.json").write_text(
        json.dumps(
            {
                "manifests": manifests,
                "filter_parameters": filter_parameters,
                "ownership_unit": "one soft label per causal state",
                "global_reservation": "best independent opportunity before immutable terminal",
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "RESULT.md").write_text(
        "# Candidate 4t v2 diagnostic result\n\n"
        "One-account route with state-level ownership and global slot commitment value. "
        "Separated development windows are out-of-fold diagnostics; `fresh` is not used "
        "for model fitting.\n\n```json\n"
        + json.dumps(result, ensure_ascii=False, indent=2, default=str)
        + "\n```\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(
        arguments.development_root,
        arguments.fresh_root,
        arguments.output,
    )


if __name__ == "__main__":
    main()
