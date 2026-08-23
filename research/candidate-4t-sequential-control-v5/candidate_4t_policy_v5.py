#!/usr/bin/env python3
"""Candidate 4t v5: market-state ownership and competing auction hypotheses."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import candidate_4t_policy_v4 as hardened

core = hardened.core
base = core.base
core._assert_feature_contract = hardened.exact_feature_contract


def _market_descriptor(frame: pd.DataFrame) -> pd.Series:
    descriptor = pd.Series("", index=frame.index, dtype="object")
    for column in (
        "entry_geometry", "entry_style", "order_type", "location_kind",
        "decision_stage",
    ):
        if column in frame:
            descriptor = descriptor + "|" + frame[column].fillna("").astype(str)
    return descriptor.str.upper()


def market_state_ownership_training(frame: pd.DataFrame) -> pd.DataFrame:
    """Prefer immediate-state first passage; otherwise use resolved consensus."""
    keys = ["period", "episode_id", "state_id", "order_time_ns"]
    resolved = frame[frame.filled & frame.resolved & frame.net_r.notna()].copy()
    if resolved.empty:
        return frame.iloc[:0].copy()
    resolved["_is_immediate_ownership_action"] = _market_descriptor(
        resolved
    ).str.contains(r"MARKET|IMMEDIATE|AT_CLOSE|CURRENT_CLOSE", regex=True)
    labels: list[dict[str, Any]] = []
    for key, group in resolved.groupby(keys, sort=False):
        immediate = group[group._is_immediate_ownership_action]
        owner = immediate if not immediate.empty else group
        record = dict(zip(keys, key))
        record[core.LABEL_OWNERSHIP] = float(owner.win.astype(float).mean())
        record[core.LABEL_OWNERSHIP_COUNT] = int(len(owner))
        record["label_ownership_source"] = (
            "IMMEDIATE_STATE_FIRST_PASSAGE" if not immediate.empty
            else "RESOLVED_ACTION_CONSENSUS"
        )
        labels.append(record)
    label_frame = pd.DataFrame(labels)
    return core.canonical_states(frame).merge(
        label_frame, on=keys, how="inner", validate="one_to_one"
    )


def hypothesis_filter_training_states(frame: pd.DataFrame) -> pd.DataFrame:
    states = market_state_ownership_training(frame)
    if states.empty:
        return states
    keys = ["period", "episode_id", "state_id", "order_time_ns"]
    aggregation: dict[str, tuple[str, str]] = {
        "raw": ("p_ownership_raw", "mean"),
        "prior": ("ownership_prior", "mean"),
        "phase": ("auction_phase", "first"),
        "family": ("family", "first"),
        "side": ("side", "first"),
    }
    grouped = frame.groupby(keys, as_index=False).agg(**aggregation)
    if "auction_progress_r" in frame:
        progress = frame.groupby(keys, as_index=False).agg(
            progress=("auction_progress_r", "mean")
        )
        grouped = grouped.merge(
            progress, on=keys, how="left", validate="one_to_one"
        )
    else:
        grouped["progress"] = 0.0
    if "auction_failure_pressure" in frame:
        failure = frame.groupby(keys, as_index=False).agg(
            failure=("auction_failure_pressure", "mean")
        )
        grouped = grouped.merge(
            failure, on=keys, how="left", validate="one_to_one"
        )
    else:
        grouped["failure"] = 0.0
    labels = states[keys + [core.LABEL_OWNERSHIP]].rename(
        columns={core.LABEL_OWNERSHIP: "target"}
    )
    return labels.merge(
        grouped, on=keys, how="left", validate="one_to_one"
    ).sort_values(keys).reset_index(drop=True)


def apply_hypothesis_filter(
    frame: pd.DataFrame,
    decay: float,
    evidence_weight: float,
    inactive_decay: float = 0.35,
    competition_weight: float = 0.25,
) -> pd.Series:
    result = pd.Series(index=frame.index, dtype=float)
    for _, episode in frame.groupby(["period", "episode_id"], sort=False):
        episode = episode.sort_values(["order_time_ns", "state_id"])
        beliefs: dict[tuple[str, str], float] = {}
        previous_progress: dict[tuple[str, str], float] = {}
        for index, row in episode.iterrows():
            family = str(row.get("family", "UNKNOWN"))
            side = str(row.get("side", "UNKNOWN"))
            key = (family, side)
            prior_l = float(base.logit(base.safe_float(row.get("prior"), 0.5)))
            raw_l = float(base.logit(base.safe_float(row.get("raw"), 0.5)))
            evidence = raw_l - prior_l
            progress = base.safe_float(row.get("progress"), 0.0)
            failure = base.safe_float(row.get("failure"), 0.0)
            phase = str(row.get("phase", "UNKNOWN"))

            # Evidence for one owner weakens stale incompatible hypotheses without
            # turning the current row's order into a source of future information.
            for other_key, other_value in list(beliefs.items()):
                if other_key == key:
                    continue
                decayed = prior_l + inactive_decay * (other_value - prior_l)
                if other_key[1] != side and evidence > 0.0:
                    decayed -= competition_weight * evidence
                beliefs[other_key] = float(np.clip(decayed, -6.0, 6.0))

            old_progress = previous_progress.get(key, 0.0)
            contradiction = (
                phase == "FAILED_REENTRY"
                or progress < -0.12
                or (old_progress > 0.25 and progress < -0.03)
                or (failure > 1.0 and progress <= 0.0)
            )
            previous = beliefs.get(key, prior_l)
            if contradiction:
                previous = prior_l
            posterior = (
                prior_l
                + decay * (previous - prior_l)
                + evidence_weight * evidence
            )
            posterior = float(np.clip(posterior, -6.0, 6.0))
            beliefs[key] = posterior
            previous_progress[key] = progress
            result.loc[index] = float(base.sigmoid(np.asarray([posterior]))[0])
    return result


def choose_hypothesis_parameters(training: pd.DataFrame) -> tuple[float, float]:
    if training.empty:
        return 0.55, 0.85
    target = pd.to_numeric(training.target, errors="coerce").fillna(0.5).to_numpy(float)
    best = (math.inf, 0.55, 0.85)
    for decay in (0.0, 0.35, 0.60, 0.80):
        for evidence in (0.55, 0.80, 1.00, 1.25):
            probability = np.clip(
                apply_hypothesis_filter(training, decay, evidence).to_numpy(float),
                0.003,
                0.997,
            )
            loss = float(-np.mean(
                target * np.log(probability)
                + (1.0 - target) * np.log(1.0 - probability)
            ))
            if (loss, decay, evidence) < best:
                best = (loss, decay, evidence)
    return float(best[1]), float(best[2])


def attach_hypothesis_ownership(
    scored: pd.DataFrame,
    parameter_training: pd.DataFrame,
) -> tuple[pd.DataFrame, tuple[float, float]]:
    keys = ["period", "episode_id", "state_id", "order_time_ns"]
    grouped = scored.groupby(keys, as_index=False).agg(
        raw=("p_ownership_raw", "mean"),
        prior=("ownership_prior", "mean"),
        phase=("auction_phase", "first"),
        family=("family", "first"),
        side=("side", "first"),
    )
    if "auction_progress_r" in scored:
        grouped = grouped.merge(
            scored.groupby(keys, as_index=False).agg(
                progress=("auction_progress_r", "mean")
            ),
            on=keys,
            how="left",
            validate="one_to_one",
        )
    else:
        grouped["progress"] = 0.0
    if "auction_failure_pressure" in scored:
        grouped = grouped.merge(
            scored.groupby(keys, as_index=False).agg(
                failure=("auction_failure_pressure", "mean")
            ),
            on=keys,
            how="left",
            validate="one_to_one",
        )
    else:
        grouped["failure"] = 0.0
    parameters = choose_hypothesis_parameters(parameter_training)
    grouped["p_ownership"] = apply_hypothesis_filter(
        grouped, parameters[0], parameters[1]
    ).to_numpy(float)
    output = scored.merge(
        grouped[["period", "state_id", "p_ownership"]],
        on=["period", "state_id"],
        how="left",
        validate="many_to_one",
    )
    return output, parameters


# v3 resolves these names at runtime; the monkey patches change only the two
# decision layers described in the v5 README.
core.ownership_training_states = market_state_ownership_training
core.filter_training_states = hypothesis_filter_training_states
core.base.attach_filtered_ownership = attach_hypothesis_ownership


def run(
    development_root: Path,
    fresh_root: Path | None,
    output: Path,
) -> dict[str, Any]:
    result = core.run(development_root, fresh_root, output)
    result["policy"] = "CANDIDATE_4T_V5_COMPETING_AUCTION_HYPOTHESES"
    result["ownership_label"] = (
        "immediate-state route-before-event-invalidation, consensus fallback"
    )
    result["sequential_belief"] = (
        "separate family/side beliefs with inactive decay and contradiction reset"
    )
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    manifest_path = output / "model_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["ownership_label"] = result["ownership_label"]
    manifest["sequential_belief"] = result["sequential_belief"]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output / "RESULT.md").write_text(
        "# Candidate 4t v5 causal diagnostic result\n\n"
        "One-account evidence for the leakage-safe, hypothesis-aware policy. "
        "Development periods are leave-one-period-out; fresh data is not used for "
        "fitting.\n\n```json\n"
        + json.dumps(result, ensure_ascii=False, indent=2, default=str)
        + "\n```\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.development_root, args.fresh_root, args.output)


if __name__ == "__main__":
    main()
