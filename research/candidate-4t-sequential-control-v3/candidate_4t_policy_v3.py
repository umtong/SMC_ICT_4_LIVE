#!/usr/bin/env python3
"""Candidate 4t v3: leakage-safe state ownership and global commitment policy."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import candidate_4t_policy as base


LABEL_OWNERSHIP = "label_ownership_state"
LABEL_OWNERSHIP_COUNT = "label_ownership_observation_count"
LABEL_SAME_WAIT = "label_same_episode_continuation"
LABEL_GLOBAL_SLOT = "label_global_slot_reservation"
FORBIDDEN_FEATURE_TOKENS = (
    "label_", "continuation_target", "global_reservation_target",
    "future_", "actual_", "outcome", "resolved", "filled", "win",
    "net_r", "mfe", "mae", "holding", "entry_wait",
)


def canonical_states(frame: pd.DataFrame) -> pd.DataFrame:
    keys = ["period", "episode_id", "state_id", "order_time_ns"]
    return (
        frame.sort_values(keys + ["action_id"])
        .drop_duplicates(keys, keep="first")
        .reset_index(drop=True)
    )


def ownership_training_states(frame: pd.DataFrame) -> pd.DataFrame:
    keys = ["period", "episode_id", "state_id", "order_time_ns"]
    resolved = frame[frame.filled & frame.resolved & frame.net_r.notna()].copy()
    if resolved.empty:
        return frame.iloc[:0].copy()
    labels = resolved.groupby(keys, as_index=False).agg(
        **{
            LABEL_OWNERSHIP: ("win", "mean"),
            LABEL_OWNERSHIP_COUNT: ("win", "size"),
        }
    )
    return canonical_states(frame).merge(
        labels, on=keys, how="inner", validate="one_to_one"
    )


def _assert_feature_contract(names: list[str], mode: str) -> None:
    violations = [
        name for name in names
        if any(token in name.lower() for token in FORBIDDEN_FEATURE_TOKENS)
    ]
    if violations:
        raise AssertionError(f"{mode} future/label leakage: {violations[:12]}")
    if mode == "ownership":
        geometry = [
            name for name in names
            if any(token in name.lower() for token in (
                "entry", "stop", "target", "route", "risk", "geometry"
            )) or name.lower().endswith("_rr")
        ]
        if geometry:
            raise AssertionError(f"ownership geometry leakage: {geometry[:12]}")


def predict_ownership(
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, float, dict[str, Any]]:
    training = ownership_training_states(train)
    testing = canonical_states(test)
    if training.empty:
        raw = np.full(len(testing), 0.5)
        disagreement = np.zeros(len(testing))
        prior = 0.5
        manifest: dict[str, Any] = {"global": [], "families": {}}
    else:
        raw, disagreement, prior, names = base.ensemble_probability(
            training, testing, LABEL_OWNERSHIP, "ownership"
        )
        _assert_feature_contract(names, "ownership")
        manifest = {"global": names, "families": {}}
        test_family = testing.get(
            "family", pd.Series("UNKNOWN", index=testing.index)
        ).fillna("UNKNOWN").astype(str)
        train_family = training.get(
            "family", pd.Series("UNKNOWN", index=training.index)
        ).fillna("UNKNOWN").astype(str)
        for family in sorted(test_family.unique()):
            train_mask = train_family.eq(family)
            test_mask = test_family.eq(family)
            subset = training.loc[train_mask]
            target = pd.to_numeric(subset[LABEL_OWNERSHIP], errors="coerce")
            if len(subset) < 160 or target.nunique(dropna=True) < 2:
                continue
            family_mean, family_std, family_prior, family_names = (
                base.ensemble_probability(
                    training,
                    testing.loc[test_mask],
                    LABEL_OWNERSHIP,
                    "ownership",
                    train_mask,
                )
            )
            _assert_feature_contract(family_names, "ownership")
            positions = np.flatnonzero(test_mask.to_numpy())
            old = raw[positions].copy()
            blend = min(0.82, len(subset) / (len(subset) + 260.0))
            raw[positions] = (1.0 - blend) * old + blend * family_mean
            disagreement[positions] = np.sqrt(
                (1.0 - blend) * disagreement[positions] ** 2
                + blend * family_std**2
                + blend * (1.0 - blend) * (family_mean - old) ** 2
            )
            manifest["families"][family] = {
                "states": int(len(subset)),
                "prior": float(family_prior),
                "blend": float(blend),
                "features": family_names,
            }
    state_scores = testing[["period", "state_id"]].copy()
    state_scores["ownership_mean"] = raw
    state_scores["ownership_std"] = disagreement
    state_scores["ownership_prior_v3"] = prior
    mapped = test[["period", "state_id"]].merge(
        state_scores,
        on=["period", "state_id"],
        how="left",
        validate="many_to_one",
    )
    return (
        mapped.ownership_mean.to_numpy(float),
        mapped.ownership_std.to_numpy(float),
        float(prior),
        manifest,
    )


def score_train_test(
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    training = train.copy()
    training["label_resolved_after_fill"] = np.where(
        training.filled, training.resolved.astype(float), np.nan
    )
    own_mean, own_std, own_prior, own_manifest = predict_ownership(training, test)
    fill_mean, fill_std, fill_prior, fill_features = base.ensemble_probability(
        training, test, "filled", "execution"
    )
    resolve_mean, resolve_std, resolve_prior, resolve_features = (
        base.ensemble_probability(
            training,
            test,
            "label_resolved_after_fill",
            "execution",
            training.filled,
        )
    )
    _assert_feature_contract(fill_features, "execution")
    _assert_feature_contract(resolve_features, "execution")
    duration_training = training[
        np.isfinite(training.terminal_minutes_label)
    ].copy()
    if len(duration_training) >= 90:
        duration_model = base.fit_ridge(
            duration_training,
            np.log1p(duration_training.terminal_minutes_label.to_numpy(float)),
            base.decision_weights(duration_training),
            "execution",
            24.0,
        )
        _assert_feature_contract(duration_model.encoder.names, "execution")
        duration = np.expm1(duration_model.predict(test))
        duration_features = duration_model.encoder.names
    else:
        median = (
            float(duration_training.terminal_minutes_label.median())
            if len(duration_training)
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


def filter_training_states(frame: pd.DataFrame) -> pd.DataFrame:
    states = ownership_training_states(frame)
    if states.empty:
        return states
    keys = ["period", "episode_id", "state_id", "order_time_ns"]
    grouped = frame.groupby(keys, as_index=False).agg(
        raw=("p_ownership_raw", "mean"),
        prior=("ownership_prior", "mean"),
        phase=("auction_phase", "first"),
    )
    if "auction_progress_r" in frame:
        grouped = grouped.merge(
            frame.groupby(keys, as_index=False).agg(
                progress=("auction_progress_r", "mean")
            ),
            on=keys,
            how="left",
            validate="one_to_one",
        )
    else:
        grouped["progress"] = 0.0
    if "auction_failure_pressure" in frame:
        grouped = grouped.merge(
            frame.groupby(keys, as_index=False).agg(
                failure=("auction_failure_pressure", "mean")
            ),
            on=keys,
            how="left",
            validate="one_to_one",
        )
    else:
        grouped["failure"] = 0.0
    labels = states[keys + [LABEL_OWNERSHIP]].rename(
        columns={LABEL_OWNERSHIP: "target"}
    )
    return labels.merge(
        grouped, on=keys, how="left", validate="one_to_one"
    ).sort_values(keys).reset_index(drop=True)


def _terminal_horizon_ns(row: pd.Series) -> float:
    start = base.safe_float(row.get("order_time_ns"), 0.0)
    terminal = base.safe_float(row.get("terminal_ns"), float("nan"))
    if math.isfinite(terminal) and terminal > start:
        return terminal
    minutes = max(
        1.0, base.safe_float(row.get("predicted_terminal_minutes"), 60.0)
    )
    return start + minutes * base.NS_PER_MINUTE


def attach_training_continuation_labels(states: pd.DataFrame) -> pd.DataFrame:
    """Create future-only labels; these columns are never supplied as predictors."""
    output = states.copy()
    output[LABEL_SAME_WAIT] = 0.0
    output[LABEL_GLOBAL_SLOT] = 0.0
    for _, period in output.groupby("period", sort=False):
        ordered = period.sort_values(["order_time_ns", "state_id"])
        indices = list(ordered.index)
        times = ordered.order_time_ns.to_numpy(float)
        episodes = ordered.episode_id.astype(str).to_numpy()
        values = np.maximum(0.0, ordered.expected_enter_log.to_numpy(float))
        horizons = np.asarray(
            [_terminal_horizon_ns(row) for _, row in ordered.iterrows()],
            dtype=float,
        )
        for left, row_index in enumerate(indices):
            same_best = 0.0
            global_best = 0.0
            for right in range(left + 1, len(indices)):
                if times[right] > horizons[left]:
                    break
                delay_hours = max(
                    0.0, (times[right] - times[left]) / base.NS_PER_HOUR
                )
                value = values[right] * math.exp(-0.035 * delay_hours)
                if episodes[right] == episodes[left]:
                    same_best = max(same_best, value)
                else:
                    global_best = max(global_best, value)
            output.loc[row_index, LABEL_SAME_WAIT] = same_best
            output.loc[row_index, LABEL_GLOBAL_SLOT] = global_best
    return output


def _fit_wait_model(
    training: pd.DataFrame,
    label: str,
) -> tuple[base.RidgeModel | None, list[str]]:
    if len(training) < 80:
        return None, []
    model = base.fit_ridge(
        training,
        training[label].to_numpy(float),
        base.decision_weights(training),
        "continuation",
        32.0,
    )
    _assert_feature_contract(model.encoder.names, "continuation")
    return model, model.encoder.names


def attach_continuation_values(
    training_labeled_states: pd.DataFrame,
    test_states: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    output = test_states.copy()
    same_model, same_features = _fit_wait_model(
        training_labeled_states, LABEL_SAME_WAIT
    )
    global_model, global_features = _fit_wait_model(
        training_labeled_states, LABEL_GLOBAL_SLOT
    )
    output["same_episode_wait_log"] = (
        np.maximum(0.0, same_model.predict(output))
        if same_model is not None
        else 0.0
    )
    output["expected_global_reservation"] = (
        np.maximum(0.0, global_model.predict(output))
        if global_model is not None
        else 0.0
    )
    output["global_commitment_cost"] = (
        output.p_fill.to_numpy(float)
        * output.expected_global_reservation.to_numpy(float)
    )
    output["expected_wait_log"] = np.maximum(
        output.same_episode_wait_log.to_numpy(float),
        output.global_commitment_cost.to_numpy(float),
    )
    output["stopping_advantage"] = (
        output.expected_enter_log.to_numpy(float)
        - output.expected_wait_log.to_numpy(float)
    )
    return output, {
        "same_episode_wait_features": same_features,
        "global_slot_features": global_features,
    }


def diagnostics(
    states: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    keep = base.keep_columns(states)
    for column in (
        "same_episode_wait_log", "expected_global_reservation",
        "global_commitment_cost",
    ):
        if column in states and column not in keep:
            keep.append(column)
    losses = (
        trades[pd.to_numeric(trades.net_r, errors="coerce") <= 0.0][keep].copy()
        if len(trades)
        else states.iloc[:0][keep].copy()
    )
    selected = (
        set(zip(orders.period.astype(str), orders.state_id.astype(str)))
        if len(orders)
        else set()
    )
    missed = states[
        states.resolved & states.net_r.notna()
        & (pd.to_numeric(states.net_r, errors="coerce") > 0.0)
    ].copy()
    mask = [
        (str(period), str(state)) not in selected
        for period, state in zip(missed.period, missed.state_id)
    ]
    missed = missed.loc[mask, keep].sort_values(
        ["period", "net_r"], ascending=[True, False]
    )
    missed = missed.groupby("period", as_index=False, group_keys=False).head(200)
    return losses, missed


def _write_outputs(
    output: Path,
    prefix: str,
    scored: pd.DataFrame,
    states: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
    replacements: pd.DataFrame,
    losses: pd.DataFrame,
    missed: pd.DataFrame,
) -> None:
    scored.to_csv(output / f"{prefix}_action_scores.csv", index=False)
    states.to_csv(output / f"{prefix}_states.csv", index=False)
    orders.to_csv(output / f"{prefix}_orders.csv", index=False)
    trades.to_csv(output / f"{prefix}_trades.csv", index=False)
    replacements.to_csv(output / f"{prefix}_replacements.csv", index=False)
    losses.to_csv(output / f"{prefix}_loss_clinic.csv", index=False)
    missed.to_csv(
        output / f"{prefix}_missed_opportunity_clinic.csv", index=False
    )


def run(
    development_root: Path,
    fresh_root: Path | None,
    output: Path,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    development = base.load_actions(development_root)
    periods = sorted(development.period.unique())
    if len(periods) < 2:
        raise ValueError("candidate 4t v3 requires separated development periods")

    oof_parts: list[pd.DataFrame] = []
    manifests: dict[str, Any] = {}
    for held_out_period in periods:
        scored, manifest = score_train_test(
            development[development.period != held_out_period],
            development[development.period == held_out_period],
        )
        oof_parts.append(scored)
        manifests[f"development_holdout:{held_out_period}"] = manifest
    development_scored = pd.concat(oof_parts, ignore_index=True, sort=False)
    filter_states = filter_training_states(development_scored)

    filtered_parts: list[pd.DataFrame] = []
    filter_parameters: dict[str, Any] = {}
    for held_out_period in periods:
        part, parameters = base.attach_filtered_ownership(
            development_scored[
                development_scored.period == held_out_period
            ].copy(),
            filter_states[filter_states.period != held_out_period],
        )
        filtered_parts.append(part)
        filter_parameters[f"development_holdout:{held_out_period}"] = {
            "decay": parameters[0],
            "evidence_weight": parameters[1],
        }
    development_scored = base.attach_enter_values(
        pd.concat(filtered_parts, ignore_index=True, sort=False)
    )
    development_state_base = base.state_best(development_scored)
    development_labeled_states = attach_training_continuation_labels(
        development_state_base
    )

    development_state_parts: list[pd.DataFrame] = []
    wait_manifests: dict[str, Any] = {}
    for held_out_period in periods:
        training_states = development_labeled_states[
            development_labeled_states.period != held_out_period
        ]
        testing_states = development_state_base[
            development_state_base.period == held_out_period
        ].copy()
        testing_states, wait_manifest = attach_continuation_values(
            training_states, testing_states
        )
        development_state_parts.append(testing_states)
        wait_manifests[f"development_holdout:{held_out_period}"] = wait_manifest
    development_states = pd.concat(
        development_state_parts, ignore_index=True, sort=False
    )
    dev_orders, dev_trades, dev_replacements, dev_summary = base.route(
        development_states
    )
    dev_losses, dev_missed = diagnostics(
        development_states, dev_orders, dev_trades
    )
    _write_outputs(
        output, "development", development_scored, development_states,
        dev_orders, dev_trades, dev_replacements, dev_losses, dev_missed,
    )

    result: dict[str, Any] = {
        "policy": "CANDIDATE_4T_V3_CAUSAL_STATE_OWNERSHIP_GLOBAL_COMMITMENT",
        "development_oof": dev_summary,
        "filter_parameters": filter_parameters,
        "manifests": manifests,
        "wait_manifests": wait_manifests,
        "input_hashes": {"development": base.input_hashes(development_root)},
        "causal_feature_contract": "no label/future/outcome fields; no action geometry in ownership",
    }

    if fresh_root is not None:
        fresh = base.load_actions(fresh_root)
        fresh_scored, fresh_manifest = score_train_test(development, fresh)
        manifests["fresh"] = fresh_manifest
        fresh_scored, parameters = base.attach_filtered_ownership(
            fresh_scored, filter_states
        )
        filter_parameters["fresh"] = {
            "decay": parameters[0],
            "evidence_weight": parameters[1],
        }
        fresh_scored = base.attach_enter_values(fresh_scored)
        fresh_states = base.state_best(fresh_scored)
        fresh_states, fresh_wait_manifest = attach_continuation_values(
            development_labeled_states, fresh_states
        )
        wait_manifests["fresh"] = fresh_wait_manifest
        orders, trades, replacements, fresh_summary = base.route(fresh_states)
        losses, missed = diagnostics(fresh_states, orders, trades)
        _write_outputs(
            output, "fresh", fresh_scored, fresh_states, orders, trades,
            replacements, losses, missed,
        )
        result["fresh"] = fresh_summary
        result["input_hashes"]["fresh"] = base.input_hashes(fresh_root)

    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output / "model_manifest.json").write_text(
        json.dumps(
            {
                "manifests": manifests,
                "wait_manifests": wait_manifests,
                "filter_parameters": filter_parameters,
                "ownership_unit": "one soft action-independent label per causal state",
                "fresh_future_columns_constructed": False,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "RESULT.md").write_text(
        "# Candidate 4t v3 causal diagnostic result\n\n"
        "The state ownership and both continuation models obey the committed causal "
        "feature contract. Development periods are leave-one-period-out; fresh data is "
        "not used for fitting or architecture selection.\n\n```json\n"
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
    run(arguments.development_root, arguments.fresh_root, arguments.output)


if __name__ == "__main__":
    main()
