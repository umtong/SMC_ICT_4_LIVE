from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from .contracts import EpisodePlan
from .policy_bridge import activate_restored_policy_paths

EPS = 1e-12
RESOLVED_OUTCOMES = {
    "TARGET_FIRST",
    "STOP_FIRST",
    "AMBIGUOUS_SAME_MINUTE",
    "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
}


@dataclass(frozen=True, slots=True)
class BundleMetadata:
    bundle_id: str
    created_at_utc: str
    training_cutoff_utc: str
    feature_columns: tuple[str, ...]
    fill_train_rows: int
    fill_positive_rate: float
    target_train_rows: int
    target_positive_rate: float
    fill_shrink: float
    target_shrink: float
    source_files: tuple[dict[str, Any], ...]
    policy_risk_fraction: float
    sklearn_version: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["feature_columns"] = list(self.feature_columns)
        payload["source_files"] = list(self.source_files)
        return payload


@dataclass(slots=True)
class ModelBundle:
    metadata: BundleMetadata
    fill_model: HistGradientBoostingClassifier
    target_model: HistGradientBoostingClassifier
    fill_base_rate: float
    target_base_rate: float

    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        joblib.dump(self, temporary, compress=3)
        temporary.replace(destination)
        destination.with_suffix(destination.suffix + ".json").write_text(
            json.dumps(self.metadata.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return destination

    @classmethod
    def load(cls, path: str | Path) -> "ModelBundle":
        source = Path(path)
        candidate = joblib.load(source)
        if not isinstance(candidate, cls):
            raise TypeError(f"unexpected bundle type: {type(candidate)!r}")
        return candidate

    def _features(self, plans: Iterable[EpisodePlan]) -> pd.DataFrame:
        rows = [dict(plan.source_row or {}) for plan in plans]
        output = pd.DataFrame(rows)
        for column in self.metadata.feature_columns:
            if column not in output:
                output[column] = 0.0
            output[column] = pd.to_numeric(output[column], errors="coerce")
        return output[list(self.metadata.feature_columns)].replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)

    def score(self, plans: Iterable[EpisodePlan], *, risk_fraction: float) -> list[EpisodePlan]:
        materialized = list(plans)
        if not materialized:
            return []
        features = self._features(materialized)
        fill_raw = self.fill_model.predict_proba(features)[:, 1]
        target_raw = self.target_model.predict_proba(features)[:, 1]
        fill = np.clip(
            self.fill_base_rate + self.metadata.fill_shrink * (fill_raw - self.fill_base_rate),
            0.02,
            0.98,
        )
        target = np.clip(
            self.target_base_rate + self.metadata.target_shrink * (target_raw - self.target_base_rate),
            0.02,
            0.98,
        )
        output: list[EpisodePlan] = []
        loss_log = math.log(max(EPS, 1.0 - risk_fraction))
        for plan, p_fill, p_target in zip(materialized, fill, target, strict=True):
            win_log = math.log(max(EPS, 1.0 + risk_fraction * plan.planned_target_net_r))
            denominator = win_log - loss_log
            breakeven = -loss_log / denominator if denominator > EPS else 1.0
            edge = float(p_target - breakeven)
            expected = float(p_fill * (p_target * win_log + (1.0 - p_target) * loss_log))
            output.append(
                replace(
                    plan,
                    expected_log_growth=expected,
                    probability_edge=edge,
                    p_fill=float(p_fill),
                    p_target_if_filled=float(p_target),
                    model_bundle_id=self.metadata.bundle_id,
                )
            )
        return output


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _features(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index)
    for column in columns:
        output[column] = pd.to_numeric(frame[column], errors="coerce") if column in frame else 0.0
    return output.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)


def _time_ns(frame: pd.DataFrame, column: str) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce") if column in frame else pd.Series(np.nan, index=frame.index)
    return pd.to_datetime(values, unit="ns", utc=True, errors="coerce")


def _fit(x: pd.DataFrame, y: pd.Series, *, random_state: int) -> tuple[HistGradientBoostingClassifier, float, float]:
    labels = pd.to_numeric(y, errors="coerce").dropna().astype(int)
    matrix = x.loc[labels.index]
    if len(labels) < 120 or labels.nunique() < 2 or int(labels.sum()) < 24 or int((1 - labels).sum()) < 24:
        raise RuntimeError(
            "not enough mature observations to build an order-capable model bundle: "
            f"rows={len(labels)}, positives={int(labels.sum())}, negatives={int((1-labels).sum())}"
        )
    model = HistGradientBoostingClassifier(
        learning_rate=0.045,
        max_iter=140,
        max_leaf_nodes=7,
        min_samples_leaf=max(12, min(30, len(labels) // 8)),
        l2_regularization=1.5,
        random_state=random_state,
    )
    model.fit(matrix, labels)
    base = float(labels.mean())
    shrink = float(len(labels) / (len(labels) + 180.0))
    return model, base, shrink


def load_training_rows(root: str | Path) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    root_path = Path(root)
    frames: list[pd.DataFrame] = []
    sources: list[dict[str, Any]] = []
    for path in sorted(root_path.glob("**/departure_actions.csv.gz")):
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        frame["source_file"] = str(path)
        frames.append(frame)
        sources.append({"path": str(path), "sha256": _sha256(path), "rows": len(frame)})
    if not frames:
        raise FileNotFoundError(f"no departure_actions.csv.gz under {root_path}")
    return pd.concat(frames, ignore_index=True, sort=False), sources


def train_bundle(
    root: str | Path,
    output: str | Path,
    *,
    cutoff: str,
    risk_fraction: float = 0.03,
) -> ModelBundle:
    activate_restored_policy_paths()
    from episode_policy_features import FEATURE_COLUMNS
    import sklearn

    frame, source_files = load_training_rows(root)
    exists = (
        frame.get("order_exists", pd.Series(False, index=frame.index))
        .astype(str)
        .str.lower()
        .isin({"true", "1", "yes"})
    )
    orders = frame[exists].copy()
    cutoff_time = pd.Timestamp(cutoff)
    cutoff_time = cutoff_time.tz_localize("UTC") if cutoff_time.tzinfo is None else cutoff_time.tz_convert("UTC")
    order_time = _time_ns(orders, "order_time_ns")
    fill_time = _time_ns(orders, "fill_time_ns")
    terminal_time = _time_ns(orders, "order_terminal_time_ns")
    resolution_time = _time_ns(orders, "resolution_time_ns")
    orders["fill_label"] = fill_time.notna().astype(int)
    orders["target_label"] = orders.get("outcome", "").astype(str).eq("TARGET_FIRST").astype(int)
    orders["resolved_label"] = orders.get("outcome", "").astype(str).isin(RESOLVED_OUTCOMES)
    orders["fill_available"] = fill_time.where(orders.fill_label.eq(1), terminal_time)
    orders["target_available"] = resolution_time.where(orders.resolved_label)
    causal = order_time.lt(cutoff_time)
    fill_train = orders.index[
        causal & orders.fill_available.notna() & orders.fill_available.lt(cutoff_time)
    ]
    target_train = orders.index[
        causal & orders.resolved_label & orders.target_available.notna() & orders.target_available.lt(cutoff_time)
    ]
    columns = tuple(FEATURE_COLUMNS)
    x = _features(orders, columns)
    fill_model, fill_base, fill_shrink = _fit(
        x.loc[fill_train], orders.loc[fill_train, "fill_label"], random_state=4100
    )
    target_model, target_base, target_shrink = _fit(
        x.loc[target_train], orders.loc[target_train, "target_label"], random_state=8100
    )
    identity_payload = {
        "cutoff": cutoff_time.isoformat(),
        "features": columns,
        "fill_rows": len(fill_train),
        "target_rows": len(target_train),
        "source_files": source_files,
        "risk_fraction": risk_fraction,
    }
    bundle_id = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    metadata = BundleMetadata(
        bundle_id=bundle_id,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        training_cutoff_utc=cutoff_time.isoformat(),
        feature_columns=columns,
        fill_train_rows=len(fill_train),
        fill_positive_rate=fill_base,
        target_train_rows=len(target_train),
        target_positive_rate=target_base,
        fill_shrink=fill_shrink,
        target_shrink=target_shrink,
        source_files=tuple(source_files),
        policy_risk_fraction=risk_fraction,
        sklearn_version=sklearn.__version__,
    )
    bundle = ModelBundle(
        metadata=metadata,
        fill_model=fill_model,
        target_model=target_model,
        fill_base_rate=fill_base,
        target_base_rate=target_base,
    )
    bundle.save(output)
    return bundle


def eligible_plans(plans: Iterable[EpisodePlan]) -> list[EpisodePlan]:
    output = [
        plan
        for plan in plans
        if plan.expected_log_growth is not None
        and plan.probability_edge is not None
        and plan.expected_log_growth > 0.0
        and plan.probability_edge > 0.0
        and plan.planned_target_net_r > 0.0
    ]
    return sorted(
        output,
        key=lambda plan: (
            plan.order_time_ns,
            -(plan.expected_log_growth or 0.0),
            -(plan.probability_edge or 0.0),
            -plan.mechanism_coherence,
            -plan.gross_rr,
            plan.episode_id,
        ),
    )
